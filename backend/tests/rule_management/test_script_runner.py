"""规则管理: 脚本执行器 (script_runner) 子进程契约测试

    :Preparation
        1. 以真实子进程方式调用 app/modules/diagnosis/script_runner.py, 与 Worker 运行时一致, 不依赖 DB/Redis;
        2. 脚本根目录使用 backend/rules_scripts (含 oom_burst.py), 另用 tmp_path 临时编写非法脚本;
        3. 调用载荷 {script_root, script_path, ctx} 经 stdin 以 JSON 传入。

    :TestStep
        1. 运行 oom_burst.py, 传入 4 条 "Out of memory" 条目(60s 窗口内)与阈值参数;
        2. 运行缺少 check(ctx) 函数的脚本;
        3. 运行不存在的脚本文件;
        4. 运行路径穿越的脚本 (../secret.py);
        5. 运行 check 返回 list 的脚本;
        6. 运行 check 返回 None 的脚本。

    :ExpectResult
        1. 退出码 0, ok=true, 命中 1 条 finding(line_no=4, 含 "4 次 OOM")且带 logs;
        2. 退出码 1, ok=false, error 含 "check(ctx)";
        3. 退出码 1, ok=false, error 含 "不存在";
        4. 退出码 1, ok=false, error 含 "逃逸";
        5. 退出码 1, ok=false, error 含 "dict";
        6. 退出码 0, ok=true, result 为空 findings/logs。

    :AfterTest
        1. tmp_path 由 pytest 自动清理;
        2. 不写 DB/Redis, 不改动 rules_scripts 目录。
"""
import json
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]  # tests/rule_management/ -> backend/
RUNNER = BACKEND / "app" / "modules" / "diagnosis" / "script_runner.py"
SCRIPTS_DIR = BACKEND / "rules_scripts"


def _run(script_root, script_path, ctx):
    payload = {"script_root": str(script_root), "script_path": script_path, "ctx": ctx}
    proc = subprocess.run([sys.executable, "-I", str(RUNNER)],
                          input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc.returncode, json.loads(proc.stdout.decode("utf-8"))


def _ctx(entries=None, rule_params=None):
    return {"rule": {"id": 1, "name": "t", "severity": "warn", "params": rule_params or {}},
            "task": {}, "entries": entries or [], "metrics": [], "file_stats": {},
            "meta": {"total_entries": len(entries or []), "total_metrics": 0, "truncated": False}}


def _oom_entries():
    return [{"line_no": i + 1, "raw": f"Jan  1 12:00:{s:02d} kernel: Out of memory: killed {i}",
             "ts": f"2026-10-10T12:00:{s:02d}+08:00", "level": "error",
             "component": "kernel", "file": "syslog", "fields": {}}
            for i, s in enumerate([0, 10, 20, 25])]


def test_oom_burst_hit():
    rc, out = _run(SCRIPTS_DIR, "oom_burst.py",
                   _ctx(_oom_entries(), {"window_seconds": 60, "threshold": 3,
                                         "keyword": "Out of memory"}))
    assert rc == 0 and out["ok"] is True
    findings = out["result"]["findings"]
    assert len(findings) == 1
    assert findings[0]["line_no"] == 4
    assert "4 次 OOM" in findings[0]["message"]
    assert out["result"]["logs"]


def test_missing_check(tmp_path):
    (tmp_path / "bad.py").write_text("def notcheck(ctx):\n    return {}\n", encoding="utf-8")
    rc, out = _run(tmp_path, "bad.py", _ctx())
    assert rc == 1 and out["ok"] is False
    assert "check(ctx)" in out["error"]


def test_missing_file(tmp_path):
    rc, out = _run(tmp_path, "nope.py", _ctx())
    assert rc == 1 and out["ok"] is False
    assert "不存在" in out["error"]


def test_path_traversal(tmp_path):
    rc, out = _run(tmp_path, "../secret.py", _ctx())
    assert rc == 1 and out["ok"] is False
    assert "逃逸" in out["error"]


def test_non_dict_return(tmp_path):
    (tmp_path / "list.py").write_text("def check(ctx):\n    return []\n", encoding="utf-8")
    rc, out = _run(tmp_path, "list.py", _ctx())
    assert rc == 1 and out["ok"] is False
    assert "dict" in out["error"]


def test_none_return_is_empty(tmp_path):
    (tmp_path / "none.py").write_text("def check(ctx):\n    return None\n", encoding="utf-8")
    rc, out = _run(tmp_path, "none.py", _ctx())
    assert rc == 0 and out["ok"] is True
    assert out["result"] == {"findings": [], "logs": []}