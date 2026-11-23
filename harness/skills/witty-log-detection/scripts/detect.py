#!/usr/bin/env python3
"""直接拉起 witty-log-detection，把异常写入 detection.json。不要阅读技能源码。"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
DEFAULT_KEYWORDS = [
    "error", "Error", "ERROR", "fail", "failed", "failure", "fault",
    "critical", "Critical", "warning", "Warning", "timeout", "panic",
    "oops", "Call Trace", "BUG:", "I/O error", "Machine check",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="对日志做异常检测并写出 detection.json")
    parser.add_argument("files", nargs="+", help="日志路径，相对当前工作区或绝对路径")
    parser.add_argument("--out", default="detection.json")
    parser.add_argument(
        "--type",
        default="log_detection_base_on_keywords",
        choices=[
            "log_detection_base_on_keywords",
            "log_detection_base_on_clustering",
            "log_detection_base_on_llm",
            "log_detection_base_on_embedding",
        ],
    )
    parser.add_argument("--keyword", action="append", default=[])
    parser.add_argument("--limit", type=int, default=200)
    return parser.parse_args()


async def call_json(session, tool: str, args: dict):
    result = await session.call_tool(tool, args)
    if result.isError:
        raise RuntimeError(f"{tool} 失败: {result.content}")
    return json.loads(result.content[0].text)


async def run(args: argparse.Namespace) -> int:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    cwd = Path.cwd()
    files = []
    for raw in args.files:
        path = Path(raw)
        if not path.is_absolute():
            path = cwd / path
        if not path.is_file():
            print(f"找不到日志: {raw}", file=sys.stderr)
            return 2
        files.append(str(path.resolve()))

    db_dir = cwd / "work" / "mcp"
    db_dir.mkdir(parents=True, exist_ok=True)
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    env["PYTHONPATH"] = str(SKILL)
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(SKILL / "src" / "server.py")],
        cwd=str(db_dir),
        env=env,
    )
    keywords = args.keyword or DEFAULT_KEYWORDS
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            created = await call_json(session, "create_log_parse_task", {
                "task_type": args.type,
                "query": "log anomaly",
                "file_path_list": files,
                "anomaly_keywords": keywords,
                "max_anomaly_log_count": args.limit,
            })
            if created.get("error"):
                print(created.get("message") or created["error"], file=sys.stderr)
                return 1
            task_id = created["task_id"]
            status = ""
            for _ in range(300):
                message = await call_json(session, "get_task_message", {"task_id": task_id})
                status = str(message.get("status") or "").lower()
                print(status, message.get("completion_precent"), file=sys.stderr)
                if status in {"successful", "failed"}:
                    break
                await asyncio.sleep(2)
            if status != "successful":
                print(f"检测未完成: {status or '无状态'}", file=sys.stderr)
                return 1
            payload = await call_json(session, "get_task_result", {
                "task_id": task_id,
                "is_anomalous": True,
                "limit": args.limit,
            })

    rows = []
    for item in payload.get("results") or []:
        file_path = item.get("file_path") or ""
        try:
            rel = str(Path(file_path).resolve().relative_to(cwd))
        except ValueError:
            rel = file_path
        rows.append({
            "file": rel,
            "line_number": item.get("offset"),
            "reason": item.get("anomaly_reason") or "",
            "snippet": item.get("content") or "",
            "score": item.get("anomaly_score"),
        })
    out = cwd / args.out
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"命中 {len(rows)} 条，已写入 {args.out}")
    return 0


def main() -> int:
    return asyncio.run(run(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
