"""脚本规则子进程入口: 读取 stdin 的 {script_root, script_path, ctx} 并执行 check(ctx).

由 rule_engine 以 `python -I script_runner.py` 方式拉起, 独立于 Worker 进程,
用于做到 per-rule 超时与崩溃隔离 (见 docs/脚本规则设计.md 第五节)。

本模块只依赖标准库, 不 import app.*, 以保证 -I 隔离模式下 (sys.path 不含 backend/)
仍可运行; stdin/stdout 一律走 binary 通道并按 UTF-8 编解码, 规避 Windows 控制台代码页问题。
"""
import importlib.util
import json
import sys
from pathlib import Path


def _load_module(script_root: Path, rel_path: str):
    """按绝对路径加载脚本模块 (接口层已校验, 此处二次防穿越兜底)."""
    target = (script_root / rel_path).resolve()
    if not target.is_relative_to(script_root):
        raise ValueError(f"脚本路径逃逸根目录: {rel_path}")
    if target.suffix != ".py" or not target.is_file():
        raise ValueError(f"脚本不存在或非 .py 文件: {rel_path}")
    spec = importlib.util.spec_from_file_location(f"_rule_script_{target.stem}", target)
    if spec is None or spec.loader is None:
        raise ValueError(f"无法加载脚本模块: {rel_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(payload: dict) -> dict:
    script_root = Path(payload["script_root"]).resolve()
    module = _load_module(script_root, payload["script_path"])
    fn = getattr(module, "check", None)
    if not callable(fn):
        raise ValueError("脚本缺少 check(ctx) 入口函数")
    result = fn(payload.get("ctx") or {})
    if result is None:
        return {"findings": [], "logs": []}
    if not isinstance(result, dict):
        raise ValueError(f"check(ctx) 应返回 dict, 实际为 {type(result).__name__}")
    return result


def main() -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        out = {"ok": True, "result": _run(payload)}
        code = 0
    except Exception as exc:  # 脚本异常不外抛, 结构化返回给父进程
        out = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        code = 1
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False, default=str).encode("utf-8"))
    sys.stdout.buffer.flush()
    return code


if __name__ == "__main__":
    sys.exit(main())