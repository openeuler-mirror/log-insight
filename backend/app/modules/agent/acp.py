"""ACP 客户端: 以子进程方式驱动 DeepSeek Harness (dsh-acp).

协议: JSON-RPC 2.0, 按行分隔 (ndjson), stdin/stdout 同时是协议通道.
参考 Agent Client Protocol: initialize / session/new / session/load /
session/prompt / session/cancel, 服务端通过 session/update 通知回推流式事件.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from collections import deque
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

UpdateHandler = Callable[[dict[str, Any]], Awaitable[None]]
PermissionHandler = Callable[[dict[str, Any]], Awaitable[str]]


class AcpError(RuntimeError):
    """ACP 调用失败 (带 JSON-RPC error code)."""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


class AcpConnection:
    """一个 ACP 子进程连接 (单会话串行使用)."""

    def __init__(
        self,
        *,
        command: str,
        cwd: Path,
        env: dict[str, str],
        on_update: UpdateHandler,
        on_permission: PermissionHandler | None = None,
    ) -> None:
        self.command = command
        self.cwd = cwd
        self.env = env
        self.on_update = on_update
        self.on_permission = on_permission
        self.proc: asyncio.subprocess.Process | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._stderr_tail: deque[str] = deque(maxlen=80)
        self._closed = False

    # ---------- 生命周期 ----------
    async def start(self) -> None:
        exe = shutil.which(self.command)
        if exe is None:
            raise AcpError(
                f"未找到智能体运行时 {self.command}，请确认镜像已安装 deepseek-harness-acp")
        self.proc = await asyncio.create_subprocess_exec(
            exe,
            cwd=str(self.cwd),
            env=self.env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        await self.request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {"terminal": False},
        }, timeout=30)

    async def close(self) -> None:
        self._closed = True
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(AcpError("连接已关闭"))
        self._pending.clear()
        if self._reader_task:
            self._reader_task.cancel()
        if self._stderr_task:
            self._stderr_task.cancel()
        proc = self.proc
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
        for task in (self._reader_task, self._stderr_task):
            if task:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    # ---------- 协议方法 ----------
    async def request(self, method: str, params: dict, *, timeout: float | None = None) -> dict:
        if self.proc is None or self.proc.stdin is None:
            raise AcpError("ACP 进程未启动")
        self._next_id += 1
        rid = self._next_id
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        self.proc.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode())
        await self.proc.stdin.drain()
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            raise AcpError(f"智能体响应超时: {method}") from None

    async def notify(self, method: str, params: dict) -> None:
        if self.proc is None or self.proc.stdin is None:
            return
        self.proc.stdin.write(
            (json.dumps({"jsonrpc": "2.0", "method": method, "params": params}) + "\n").encode())
        await self.proc.stdin.drain()

    async def _request_when_ready(self, method: str, params: dict,
                                  *, timeout: float, ready_timeout: float = 8.0) -> dict:
        """pi-ai 路由由 settings 异步注册; 首次请求等它就绪, 避免 provider 未注册."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + ready_timeout
        while True:
            try:
                return await self.request(method, params, timeout=timeout)
            except AcpError as exc:
                if "no adapter registered" not in str(exc) or loop.time() >= deadline:
                    raise
                await asyncio.sleep(0.25)

    async def new_session(self, cwd: Path,
                          mcp_servers: list[dict] | None = None) -> str:
        res = await self.new_session_result(cwd, mcp_servers)
        sid = res.get("sessionId") or res.get("session_id")
        if not sid:
            raise AcpError("session/new 未返回 sessionId")
        return str(sid)

    async def new_session_result(self, cwd: Path,
                                 mcp_servers: list[dict] | None = None,
                                 *, ready_timeout: float = 8.0) -> dict:
        return await self._request_when_ready("session/new", {
            "cwd": str(cwd),
            "mcpServers": mcp_servers or [],
        }, timeout=30, ready_timeout=ready_timeout)

    async def set_config_option(self, session_id: str, config_id: str,
                                value: str) -> dict:
        """DSH 会话配置项 (model / effort), 由 ACP 校验取值."""
        return await self.request("session/set_config_option", {
            "sessionId": session_id,
            "configId": config_id,
            "value": value,
        }, timeout=20)

    async def set_reasoning(self, session_id: str, value: str,
                            options: list[dict] | None = None) -> str:
        """设置推理强度; 配置项 id 随 DSH 版本不同 (effort / reasoning_effort).

        返回实际生效的 id; 会话没有该配置项时返回空串.
        """
        if not value:
            return ""
        ids = [str(o.get("id") or "") for o in options or []]
        candidates = [cid for cid in ("effort", "reasoning_effort")
                      if not ids or cid in ids]
        last: AcpError | None = None
        for cid in candidates:
            try:
                await self.set_config_option(session_id, cid, value)
                return cid
            except AcpError as exc:
                last = exc
        if last is not None:
            raise last
        return ""

    async def load_session(self, session_id: str, cwd: Path,
                           mcp_servers: list[dict] | None = None) -> None:
        await self._request_when_ready("session/load", {
            "sessionId": session_id,
            "cwd": str(cwd),
            "mcpServers": mcp_servers or [],
        }, timeout=30)

    async def prompt(self, session_id: str, text: str, *,
                     timeout: float = 600) -> dict:
        return await self.request("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": text}],
        }, timeout=timeout)

    async def cancel(self, session_id: str) -> None:
        await self.notify("session/cancel", {"sessionId": session_id})

    # ---------- 内部 ----------
    async def _read_loop(self) -> None:
        assert self.proc and self.proc.stdout
        stdout = self.proc.stdout
        while not self._closed:
            try:
                line = await stdout.readline()
            except (ValueError, asyncio.LimitOverrunError):
                logger.warning("ACP 单行消息超限, 已跳过")
                continue
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                logger.debug("ACP 非 JSON 输出: %s", line[:200])
                continue
            await self._dispatch(msg)
        # 进程退出: 结束所有等待中的请求 (先回收真实退出码, 否则恒为 None)
        proc = self.proc
        if proc is not None and proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass
        code = proc.returncode if proc else None
        detail = "；".join(list(self._stderr_tail)[-3:])
        for fut in self._pending.values():
            if not fut.done():
                hint = f"（{detail}）" if detail else ""
                fut.set_exception(AcpError(f"智能体进程已退出 (code={code}){hint}"))
        self._pending.clear()

    async def _dispatch(self, msg: dict) -> None:
        if "id" in msg and "method" in msg:
            asyncio.create_task(self._handle_server_request(msg))
            return
        if "id" in msg:
            fut = self._pending.pop(int(msg["id"]), None)
            if fut and not fut.done():
                if "error" in msg:
                    err = msg["error"] or {}
                    text = err.get("message") or "ACP 调用失败"
                    data = err.get("data")
                    if isinstance(data, dict):
                        detail = data.get("details") or data.get("detail") or ""
                        if detail:
                            text = f"{text}: {detail}"
                    logger.warning("ACP 调用失败: %s; stderr=%s", text,
                                   " | ".join(list(self._stderr_tail)[-4:]))
                    fut.set_exception(AcpError(text, err.get("code")))
                else:
                    fut.set_result(msg.get("result") or {})
            return
        method = msg.get("method")
        params = msg.get("params") or {}
        if method == "session/update":
            update = params.get("update") or params
            try:
                await self.on_update(update)
            except Exception:
                logger.exception("处理 ACP 事件失败")

    async def _handle_server_request(self, msg: dict) -> None:
        rid = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        if method == "session/request_permission":
            option_id = "reject_once"
            if self.on_permission is not None:
                try:
                    option_id = await self.on_permission(params)
                except Exception:
                    logger.exception("权限处理失败")
            else:
                options = params.get("options") or []
                ids = {o.get("optionId") or o.get("option_id") for o in options}
                option_id = next(
                    (x for x in ("allow_always", "allow-always", "allow_once", "allow-once")
                     if x in ids),
                    "reject_once")
            self._respond(rid, {"outcome": {"outcome": "selected", "optionId": option_id}})
            return
        self._respond_error(rid, -32601, f"不支持的方法: {method}")

    def _respond(self, rid: Any, result: dict) -> None:
        if self.proc and self.proc.stdin and not self.proc.stdin.is_closing():
            self.proc.stdin.write(
                (json.dumps({"jsonrpc": "2.0", "id": rid, "result": result},
                            ensure_ascii=False) + "\n").encode())

    def _respond_error(self, rid: Any, code: int, message: str) -> None:
        if self.proc and self.proc.stdin and not self.proc.stdin.is_closing():
            self.proc.stdin.write(
                (json.dumps({"jsonrpc": "2.0", "id": rid,
                             "error": {"code": code, "message": message}},
                            ensure_ascii=False) + "\n").encode())

    async def _drain_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        while not self._closed:
            line = await self.proc.stderr.readline()
            if not line:
                break
            text = line.decode(errors="replace").rstrip()
            if not text:
                continue
            self._stderr_tail.append(text)
            logger.info("dsh-acp: %s", text[:500])


def _yaml(value: str) -> str:
    """JSON 字符串同时是合法的 YAML 双引号标量."""
    return json.dumps(str(value), ensure_ascii=False)


def _write_settings(base_dir: Path, *, route: str, label: str, model: str,
                    model_name: str, api_base: str, protocol: str,
                    api: str) -> None:
    """写 llm-pi-ai 路由: 每个会话独立 DSH_HOME, 请求时重新读取."""
    lines = [
        "llm-pi-ai:",
        "  providers:",
        f"    {route}:",
        f"      apiKeyEnv: {_yaml('DSH_LLM_API_KEY')}",
        f"      displayName: {_yaml(label)}",
    ]
    if api_base:
        lines.append(f"      baseURL: {_yaml(api_base)}")
    if api:
        lines.append(f"      api: {_yaml(api)}")
    lines += [
        "      models:",
        f"        - id: {_yaml(model)}",
        f"          name: {_yaml(model_name or model)}",
    ]
    dsh_home = base_dir / "home" / ".dsh"
    dsh_home.mkdir(parents=True, exist_ok=True)
    (dsh_home / "settings.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_env(base_dir: Path, *, api_key: str, model: str,
              api_base: str = "", protocol: str = "messages",
              permission_mode: str = "workspace-write",
              provider: str = "deepseek", model_name: str = "") -> dict[str, str]:
    """构造 ACP 子进程环境 (只暴露必要变量, 不继承服务端全部环境)."""
    from app.modules.agent.providers import PI_AI_API, display_name, spec_of

    spec = spec_of(provider)
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(base_dir / "home"),
        "DSH_HOME": str(base_dir / "home" / ".dsh"),
        "DSH_SESSIONS_ROOT": str(base_dir / ".sessions"),
        "DSH_PERMISSION_MODE": permission_mode,
        "NO_COLOR": "1",
        "TERM": "dumb",
    }
    if os.name == "nt":
        # Windows: node 及其批处理垫片依赖 %SystemRoot%/windir, 缺失时子进程静默秒退
        # (无 stdout/stderr), 表现为「智能体进程已退出 (code=None)」。
        for name in ("SystemRoot", "windir"):
            value = os.environ.get(name)
            if value:
                env[name] = value
    label = display_name(spec.id, model, model_name)
    if spec.native == "deepseek":
        env.update({
            "DEEPSEEK_API_KEY": api_key,
            "DEEPSEEK_MODEL": model,
            "DEEPSEEK_PROTOCOL": protocol if protocol in spec.protocols else "messages",
            "DEEPSEEK_MODELS_JSON": json.dumps(
                [{"id": model, "name": label, "contextWindow": 1000000}],
                ensure_ascii=False),
        })
        if api_base:
            env["DEEPSEEK_BASE_URL"] = api_base
    else:
        # pi-ai 路由: 由入口脚本按 DSH_PROVIDER 选择, 密钥走独立环境变量
        env.update({
            "DSH_PROVIDER": spec.route,
            "DSH_MODEL": model,
            "DSH_LLM_API_KEY": api_key,
        })
        _write_settings(
            base_dir, route=spec.route, label=spec.label, model=model,
            model_name=label, api_base=api_base,
            protocol=protocol,
            api=PI_AI_API.get(protocol, "openai-completions") if spec.requires_base else "",
        )
    (base_dir / "home" / ".dsh").mkdir(parents=True, exist_ok=True)
    (base_dir / ".sessions").mkdir(parents=True, exist_ok=True)
    return env
