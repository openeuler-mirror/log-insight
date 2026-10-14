"""oom_burst.py — 窗口内 OOM 重复次数超过阈值则告警.

规则配置示例:
    script_params = {"window_seconds": 60, "threshold": 3, "keyword": "Out of memory"}

契约: 暴露 check(ctx) 入口; ctx 结构与返回格式见 docs/脚本规则设计.md。
"""
from datetime import datetime


def check(ctx: dict) -> dict:
    params = ctx["rule"]["params"]
    window = int(params.get("window_seconds", 60))
    threshold = int(params.get("threshold", 3))
    keyword = params.get("keyword", "Out of memory")

    hits = [e for e in ctx["entries"] if keyword in e["raw"] and e.get("ts")]
    hits.sort(key=lambda e: e["ts"])

    findings, logs = [], []
    i = 0
    while i < len(hits):
        window_hits = [hits[i]]
        for e in hits[i + 1:]:
            if _seconds_between(hits[i]["ts"], e["ts"]) <= window:
                window_hits.append(e)
            else:
                break
        if len(window_hits) >= threshold:
            e = window_hits[-1]
            findings.append({
                "line_no": e["line_no"],
                "matched_text": keyword,
                "message": f"{window} 秒内第 {len(window_hits)} 次 OOM，疑似内存泄漏或超卖",
            })
            logs.append(f"{hits[i]['ts']} 起窗口命中 {len(window_hits)} 次")
            i += len(window_hits)
        else:
            i += 1
    return {"findings": findings, "logs": logs}


def _seconds_between(a: str, b: str) -> float:
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()
