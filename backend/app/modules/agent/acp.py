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
import shlex
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
        argv = shlex.split(self.command)
        if not argv:
            raise AcpError("未配置智能体运行时命令")
        exe = shutil.which(argv[0])
        if exe is None:
            raise AcpError(
                f"未找到智能体运行时 {argv[0]}，请确认镜像已安装 @deepseek-ai/dsh")
        self.proc = await asyncio.create_subprocess_exec(
            exe, *argv[1:],
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

    async def new_session_result(self, cwd: Path,
                                 mcp_servers: list[dict] | None = None) -> dict:
        """新建会话; 返回 {sessionId, configOptions}."""
        return await self.request("session/new", {
            "cwd": str(cwd),
            "mcpServers": mcp_servers or [],
        }, timeout=30)

    async def resume_session(self, session_id: str, cwd: Path,
                             mcp_servers: list[dict] | None = None) -> dict:
        """恢复已持久化的会话; 返回 {configOptions} (不回放历史更新)."""
        return await self.request("session/resume", {
            "sessionId": session_id,
            "cwd": str(cwd),
            "mcpServers": mcp_servers or [],
        }, timeout=30)

    async def close_session(self, session_id: str) -> None:
        await self.request("session/close", {"sessionId": session_id}, timeout=15)

    async def set_config_option(self, session_id: str, config_id: str,
                                value: str) -> dict:
        """会话配置项 (model / reasoning_effort), 取值由 ACP 校验."""
        return await self.request("session/set_config_option", {
            "sessionId": session_id,
            "configId": config_id,
            "value": value,
        }, timeout=20)

    async def set_reasoning(self, session_id: str, value: str) -> None:
        """设置推理强度; 模型不支持时由调用方忽略错误."""
        if value:
            await self.set_config_option(session_id, "reasoning_effort", value)

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


def _dsh_home(base_dir: Path) -> Path:
    home = base_dir / "home" / ".dsh"
    home.mkdir(parents=True, exist_ok=True)
    # 本机已登录 DSH 账号时复用该登录态 (容器内通常不存在, 不影响 API Key 通道)
    source = Path.home() / ".dsh" / ".credentials.yaml"
    target = home / ".credentials.yaml"
    if source.is_file() and not target.exists():
        try:
            target.symlink_to(source)
        except OSError:
            pass
    return home


def _write_deepseek_settings(home: Path, *, model: str, model_name: str,
                             api_base: str = "") -> None:
    """llm-deepseek 路由: 固定单模型目录, 兼容官方 API 与协议兼容网关."""
    lines = [
        "llm-deepseek:",
        f"  apiKeyEnv: {_yaml('DEEPSEEK_API_KEY')}",
    ]
    if api_base:
        lines.append(f"  baseURL: {_yaml(api_base)}")
    lines += [
        "  models:",
        f"    - id: {_yaml(model)}",
        f"      name: {_yaml(model_name or model)}",
    ]
    (home / "settings.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_pi_ai_settings(home: Path, *, route: str, label: str, model: str,
                          model_name: str, api_base: str, api: str) -> None:
    """llm-pi-ai 路由: 每个会话独立 DSH_HOME, 进程启动时读取."""
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
    (home / "settings.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_env(base_dir: Path, *, api_key: str, model: str,
              api_base: str = "", protocol: str = "messages",
              permission_mode: str = "workspace-write",
              provider: str = "deepseek", model_name: str = "") -> dict[str, str]:
    """构造 ACP 子进程环境 (只暴露必要变量, 不继承服务端全部环境)."""
    from app.modules.agent.providers import (
        PI_AI_API, display_name, route_of, spec_of)

    spec = spec_of(provider)
    home = _dsh_home(base_dir)
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(base_dir / "home"),
        "DSH_HOME": str(home),
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
    route = route_of(spec.id, protocol)
    if spec.native == "deepseek" and route == "deepseek-official":
        if api_key:
            env["DEEPSEEK_API_KEY"] = api_key
        if api_base:
            env["DEEPSEEK_BASE_URL"] = api_base
        _write_deepseek_settings(home, model=model, model_name=label, api_base=api_base)
    else:
        # pi-ai 路由 (含 DeepSeek 兼容网关): 密钥走独立环境变量
        env["DSH_LLM_API_KEY"] = api_key
        if not api_base and route == "deepseek-compat":
            api_base = "https://api.deepseek.com/v1"
        _write_pi_ai_settings(
            home, route=route, label=spec.label, model=model,
            model_name=label, api_base=api_base,
            api=PI_AI_API.get(protocol, "openai-completions"),
        )
    return env
