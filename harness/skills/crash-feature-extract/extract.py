#!/usr/bin/env python3
"""从内核日志或 vmcore 提取崩溃特征，向 stdout 输出 JSON。不访问知识库。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
sys.path.insert(0, str(SRC))

from crash_matcher.extractor.calltrace import parse_calltrace_functions  # noqa: E402
from crash_matcher.extractor.crash_cmd import run_crash_analysis  # noqa: E402
from crash_matcher.extractor.dmesg import parse_dmesg  # noqa: E402
from crash_matcher.extractor.signature import compute_signature  # noqa: E402


def _is_crash(text: str, feature) -> bool:
    if feature.bug_key or feature.rip:
        return True
    if feature.bug_type and feature.bug_type != "unknown":
        return True
    lowered = text.lower()
    return "call trace" in lowered or "kernel panic" in lowered or "oops:" in lowered


def _payload(feature, host, text: str) -> dict:
    crashed = _is_crash(text, feature)
    signature = compute_signature(feature) if crashed and feature.bug_type else ""
    data = {
        "matched_crash": crashed,
        "signature": signature,
        "crash_features": feature.model_dump(),
        "host_features": host.model_dump(),
    }
    if crashed and not data["crash_features"].get("call_trace_signature"):
        raw = feature.call_trace_text or feature.call_trace or ""
        if raw:
            data["crash_features"]["call_trace_signature"] = parse_calltrace_functions(raw)
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="提取内核崩溃特征")
    parser.add_argument("-f", "--file", help="dmesg / kern / vmcore-dmesg 路径")
    parser.add_argument("--vmcore", help="vmcore 路径")
    parser.add_argument("--vmlinux", help="与 vmcore 匹配的 vmlinux")
    args = parser.parse_args()

    if args.vmcore:
        if not args.vmlinux:
            print(json.dumps({"matched_crash": False, "error": "分析 vmcore 需要同时提供 --vmlinux"}, ensure_ascii=False))
            return 2
        feature, host, _hw = run_crash_analysis(args.vmcore, args.vmlinux)
        if feature is None:
            print(json.dumps({"matched_crash": False, "error": "crash 命令失败或未安装"}, ensure_ascii=False))
            return 1
        text = feature.call_trace_text or feature.bug or ""
        print(json.dumps(_payload(feature, host, text), ensure_ascii=False))
        return 0

    if not args.file:
        print(json.dumps({"matched_crash": False, "error": "请提供 --file 或 --vmcore"}, ensure_ascii=False))
        return 2
    path = Path(args.file)
    if not path.is_file():
        print(json.dumps({"matched_crash": False, "error": f"文件不存在: {path}"}, ensure_ascii=False))
        return 2
    text = path.read_text(errors="replace")
    feature, host, _hw = parse_dmesg(text)
    print(json.dumps(_payload(feature, host, text), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
