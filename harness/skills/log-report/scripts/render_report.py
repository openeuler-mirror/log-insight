#!/usr/bin/env python3
"""校验日志报告 JSON 并渲染为自包含 HTML。"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

REQUIRED = ("report_id", "summary", "timeline", "analysis", "actions")
STATUS = {"confirmed", "suspected", "insufficient"}


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def check(data: dict) -> None:
    if not isinstance(data, dict):
        fail("报告必须是 JSON 对象")
    for key in REQUIRED:
        if key not in data:
            fail(f"缺少字段 {key}")
    if not str(data["report_id"]).strip():
        fail("report_id 不能为空")
    summary = data["summary"]
    if not isinstance(summary, dict) or not summary.get("phenomenon") or not summary.get("scope"):
        fail("summary 需要 phenomenon 和 scope")
    if not isinstance(data["timeline"], list):
        fail("timeline 必须是数组")
    for item in data["timeline"]:
        if not isinstance(item, dict) or "file" not in item or "line" not in item or "text" not in item:
            fail("timeline 每一项需要 file、line、text")
    analysis = data["analysis"]
    if not isinstance(analysis, dict) or analysis.get("status") not in STATUS:
        fail("analysis.status 必须是 confirmed、suspected 或 insufficient")
    if not isinstance(analysis.get("evidence"), list):
        fail("analysis.evidence 必须是数组")
    actions = data["actions"]
    if not isinstance(actions, dict) or not isinstance(actions.get("steps"), list):
        fail("actions.steps 必须是数组")
    crash = data.get("kernel_crash")
    if crash is not None and not isinstance(crash, dict):
        fail("kernel_crash 必须是对象或 null")
    vm = data.get("vmcore")
    if vm is not None:
        if not isinstance(vm, dict) or vm.get("confidence") not in {"高", "中", "低", "疑似硬件"}:
            fail("vmcore.confidence 必须是 高、中、低 或 疑似硬件")


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


_STATUS = {
    "confirmed": ("已确认", "ok"),
    "suspected": ("疑似", "warn"),
    "insufficient": ("证据不足", "muted"),
}
_RELATION = {
    "independent": "独立故障",
    "causes": "导致",
    "correlated": "相关",
}


def _items(values) -> str:
    rows = "".join(f"<li>{esc(item)}</li>" for item in values or [])
    return f"<ol>{rows}</ol>" if rows else "<p class='empty'>无</p>"


def render(data: dict) -> str:
    summary = data["summary"]
    analysis = data["analysis"]
    actions = data["actions"]
    label, tone = _STATUS.get(analysis.get("status"), ("未知", "muted"))
    timeline = []
    for item in data["timeline"]:
        timeline.append(
            "<tr>"
            f"<td class='loc'>{esc(item['file'])}:{esc(item['line'])}</td>"
            f"<td class='when'>{esc(item.get('time') or '—')}</td>"
            f"<td><code>{esc(item['text'])}</code></td>"
            "</tr>"
        )
    evidence = []
    for index, ev in enumerate(analysis["evidence"], start=1):
        evidence.append(
            "<article class='card'>"
            f"<div class='card-no'>{index:02d}</div>"
            "<div>"
            f"<h3>{esc(ev.get('title'))}</h3>"
            f"<pre>{esc(ev.get('quote'))}</pre>"
            f"<p>{esc(ev.get('reasoning'))}</p>"
            "</div></article>"
        )
    faults = []
    for fault in data.get("faults") or []:
        relation = _RELATION.get(fault.get("relation"), fault.get("relation") or "")
        faults.append(
            "<li>"
            f"<b>{esc(fault.get('id'))}</b>"
            f"<span class='pill'>{esc(relation)}</span>"
            f"<p>{esc(fault.get('summary'))}</p>"
            "</li>"
        )
    appendix = []
    crash = data.get("kernel_crash")
    if crash:
        appendix.append(
            "<section class='block'>"
            "<h2><span>附录</span>内核崩溃</h2>"
            "<div class='facts'>"
            f"<div><span>类型</span><b>{esc(crash.get('bug_type') or '—')}</b></div>"
            f"<div><span>RIP</span><b>{esc(crash.get('rip') or '—')}</b></div>"
            f"<div><span>函数</span><b>{esc(crash.get('rip_function') or '—')}</b></div>"
            "</div>"
            f"<p class='note'>指纹 {esc(crash.get('signature') or '—')}</p>"
            f"<pre>{esc(crash.get('call_trace_text') or '')}</pre>"
            "</section>"
        )
    vm = data.get("vmcore")
    if vm:
        appendix.append(
            "<section class='block'>"
            "<h2><span>附录</span>vmcore</h2>"
            f"<p class='note'>置信度 {esc(vm.get('confidence') or '—')}</p>"
            f"<p>{esc(vm.get('conclusion') or '')}</p>"
            f"<p class='note'>{esc(vm.get('note') or '')}</p>"
            "</section>"
        )
    fault_block = ""
    if faults:
        fault_block = (
            "<section class='block'><h2><span>04</span>故障关系</h2>"
            f"<ul class='faults'>{''.join(faults)}</ul></section>"
        )
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(data['report_id'])}</title>
<style>
:root {{
  --ink:#222b30; --muted:#5f6d72; --line:#e4e9e7; --bg:#f4f7f6; --paper:#fff;
  --accent:#27685b; --accent-soft:#e7f2ee; --shadow:0 1px 2px rgba(23,42,36,.05);
}}
* {{ box-sizing:border-box; }}
body {{
  margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.7 "IBM Plex Sans","PingFang SC","Microsoft YaHei",sans-serif;
}}
.wrap {{ max-width:1080px; margin:0 auto; padding:28px 32px 48px; }}
header {{
  display:flex; justify-content:space-between; gap:16px; align-items:flex-end;
  padding-bottom:18px; border-bottom:1px solid var(--line); margin-bottom:22px;
}}
.kicker {{ font-size:12px; letter-spacing:.16em; color:var(--muted); font-weight:650; }}
h1 {{ margin:6px 0 0; font-size:26px; line-height:1.35; letter-spacing:-.4px; }}
.badge {{
  flex:none; border-radius:999px; padding:4px 12px; font-size:12px; font-weight:650;
  background:var(--accent-soft); color:var(--accent);
}}
.badge.warn {{ background:#faf1de; color:#8a5f22; }}
.badge.muted {{ background:#eef1f0; color:var(--muted); }}
.hero, .block {{
  background:var(--paper); border:1px solid var(--line); border-radius:12px;
  box-shadow:var(--shadow); padding:22px 24px; margin-top:18px;
}}
.hero {{ border-top:3px solid var(--accent); }}
.hero p {{ margin:8px 0 0; }}
.facts {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; margin-top:16px; }}
.facts div {{ min-width:0; }}
.facts span, .card-no {{ display:block; color:var(--muted); font-size:12px; }}
.facts b {{ display:block; margin-top:4px; font-weight:600; }}
h2 {{ display:flex; align-items:center; gap:10px; margin:0 0 14px; font-size:18px; }}
h2 span {{
  background:var(--accent); color:#fff; border-radius:6px; padding:2px 8px;
  font:12px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace;
}}
.card {{
  display:grid; grid-template-columns:42px minmax(0,1fr); gap:12px;
  border-top:1px solid var(--line); padding:16px 0;
}}
.card:first-of-type {{ border-top:0; padding-top:0; }}
.card h3 {{ margin:0 0 8px; font-size:15px; }}
.card p, .note {{ margin:8px 0 0; color:var(--muted); }}
pre, code {{
  font:12.5px/1.7 ui-monospace,SFMono-Regular,Consolas,"Noto Sans Mono CJK SC",monospace;
}}
pre {{
  margin:0; padding:12px 14px; white-space:pre-wrap; word-break:break-word;
  background:#f3f7f5; border:1px solid var(--line); border-radius:8px;
}}
table {{ width:100%; border-collapse:collapse; }}
th, td {{ text-align:left; vertical-align:top; padding:10px 12px; border-bottom:1px solid var(--line); }}
th {{ color:var(--muted); font-size:12px; font-weight:650; }}
td code {{ background:var(--accent-soft); padding:1px 5px; border-radius:4px; }}
.loc, .when {{ color:var(--muted); font-size:12px; white-space:nowrap; }}
ol {{ margin:0; padding-left:1.2em; }}
li {{ margin:8px 0; }}
.cols {{ display:grid; grid-template-columns:1fr 1fr; gap:18px; }}
.cols h3 {{ margin:0 0 8px; font-size:14px; color:var(--muted); }}
.empty {{ color:var(--muted); }}
.faults {{ list-style:none; margin:0; padding:0; }}
.faults li {{ border-top:1px solid var(--line); padding-top:12px; }}
.pill {{
  margin-left:8px; font-size:12px; color:var(--accent); background:var(--accent-soft);
  border-radius:999px; padding:2px 8px;
}}
@media (max-width:720px) {{
  .wrap {{ padding:18px 14px 32px; }}
  header, .facts, .cols, .card {{ display:block; }}
  .badge {{ margin-top:10px; }}
  .loc, .when {{ white-space:normal; }}
}}
</style></head><body>
<div class="wrap">
<header>
  <div><div class="kicker">LOGINSIGHT</div><h1>{esc(data['report_id'])}</h1></div>
  <div class="badge {tone}">{label}</div>
</header>
<section class="hero">
  <div class="kicker">现象</div>
  <p>{esc(summary.get('phenomenon'))}</p>
  <div class="facts">
    <div><span>范围</span><b>{esc(summary.get('scope') or '—')}</b></div>
    <div><span>时间</span><b>{esc(summary.get('time_range') or '—')}</b></div>
    <div><span>结论</span><b>{esc(analysis.get('conclusion') or '—')}</b></div>
  </div>
</section>
<section class="block">
  <h2><span>01</span>证据</h2>
  {''.join(evidence) or "<p class='empty'>没有证据条目</p>"}
</section>
<section class="block">
  <h2><span>02</span>关键日志</h2>
  <table><thead><tr><th>位置</th><th>时间</th><th>原文</th></tr></thead>
  <tbody>{''.join(timeline)}</tbody></table>
</section>
<section class="block">
  <h2><span>03</span>处置</h2>
  {_items(actions.get('steps'))}
  <div class="cols">
    <div><h3>如何验证</h3>{_items(actions.get('verification'))}</div>
    <div><h3>还缺什么</h3>{_items(actions.get('missing'))}</div>
  </div>
</section>
{fault_block}
{''.join(appendix)}
</div></body></html>
"""


def main() -> int:
    if len(sys.argv) != 3:
        print("用法: render_report.py report.json report.html", file=sys.stderr)
        return 2
    src, dest = Path(sys.argv[1]), Path(sys.argv[2])
    data = json.loads(src.read_text(encoding="utf-8"))
    check(data)
    dest.write_text(render(data), encoding="utf-8")
    print(dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
