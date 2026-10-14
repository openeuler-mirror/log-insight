"""clock_skew.py — 日志时间戳回退检测 (时钟漂移 / 时区错乱).

规则配置示例:
    script_params = {"min_back_seconds": 5, "max_report": 20}

原理: 同一文件内日志时间戳应单调不减; 若后续行时间戳比前一行倒退
超过 min_back_seconds, 判为一次回退 (NTP 校时跳变、时区配置错误、机器假死恢复等)。
跨文件时重置基准, 避免不同文件的时间区间差异造成误报。

契约: 暴露 check(ctx) 入口; ctx 结构与返回格式见 docs/脚本规则设计.md。
"""
from datetime import datetime


def check(ctx: dict) -> dict:
    params = ctx["rule"]["params"]
    min_back = float(params.get("min_back_seconds", 5))
    max_report = int(params.get("max_report", 20))

    cur_file = None
    last_ts = None      # (datetime, 原始字符串) 上一行有效时间戳
    skew_count = 0
    findings = []

    for e in ctx["entries"]:
        if e.get("file") != cur_file:   # 换文件: 重置基准
            cur_file = e.get("file")
            last_ts = None
        raw_ts = e.get("ts")
        if not raw_ts:
            continue
        try:
            cur = datetime.fromisoformat(raw_ts)
        except ValueError:
            continue
        if last_ts is not None:
            back = (last_ts[0] - cur).total_seconds()
            if back >= min_back:
                skew_count += 1
                if len(findings) < max_report:
                    findings.append({
                        "line_no": e["line_no"],
                        "matched_text": raw_ts,
                        "message": f"时间戳回退 {back:.0f}s: {last_ts[1]} → {raw_ts}",
                        "severity": "warn",
                    })
        last_ts = (cur, raw_ts)

    return {"findings": findings, "logs": [f"共检测到 {skew_count} 次时间戳回退"]}