"""从诊断工作区的 DSH 会话记录里还原一次工具调用."""
from __future__ import annotations

import json
from pathlib import Path

CLIP = 4000


def _clip(text: str) -> str:
    from app.modules.agent.service import redact_server_path
    text = redact_server_path(text)
    if len(text) <= CLIP:
        return text
    return text[:CLIP] + "\n…已截断"


def _loads(line: str) -> dict | None:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _events(path: Path) -> list[dict]:
    out: list[dict] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                item = _loads(line)
                if item is not None:
                    out.append(item)
    except OSError:
        return []
    return out


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return str(content.get("text") or "")
    if isinstance(content, list):
        return "".join(_text_of(part) for part in content)
    return ""


def _result_text(event: dict) -> tuple[str, bool]:
    message = (event.get("data") or {}).get("message") or {}
    blocks = message.get("content") or []
    chunks: list[str] = []
    failed = False
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("isError") is True:
            failed = True
        chunks.append(_text_of(block.get("content")))
    return _clip("".join(chunks).strip()), failed


def _call_id(event: dict) -> str:
    data = event.get("data") or {}
    if event.get("type") == "tool/call":
        return str(data.get("callId") or "")
    message = data.get("message") or {}
    source = message.get("source") or {}
    return str(source.get("callId") or "")


def _description(arguments: str) -> str:
    try:
        data = json.loads(arguments)
    except json.JSONDecodeError:
        return ""
    if isinstance(data, dict):
        return str(data.get("description") or "")
    return ""


def _child_label(events: list[dict]) -> str:
    for event in events:
        if event.get("type") == "subagent/descriptor":
            return str((event.get("data") or {}).get("label") or "")
    return ""


def _child_steps(events: list[dict]) -> list[dict]:
    steps: list[dict] = []
    tools: dict[str, dict] = {}
    for event in events:
        kind = event.get("type")
        if kind == "assistant/message":
            content = ((event.get("data") or {}).get("message") or {}).get("content") or []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "reasoning":
                    text = _clip(str(block.get("text") or "").strip())
                    if text:
                        steps.append({"kind": "thought", "name": "", "status": "done", "text": text})
        elif kind == "tool/call":
            data = event.get("data") or {}
            call_id = str(data.get("callId") or "")
            step = {
                "kind": "tool",
                "name": str(data.get("name") or ""),
                "status": "running",
                "text": _clip(str(data.get("arguments") or "")),
            }
            steps.append(step)
            if call_id:
                tools[call_id] = step
        elif kind == "tool/result":
            call_id = _call_id(event)
            text, failed = _result_text(event)
            step = tools.get(call_id)
            if step is None:
                continue
            step["status"] = "failed" if failed else "done"
            if text:
                step["text"] = text
    return steps


def read_tool_trace(ws: Path, call_id: str) -> dict | None:
    """找到这次工具调用。工作区没有会话记录时返回 None."""
    root = ws / ".sessions"
    if not root.is_dir():
        return None
    files = list(root.rglob("session.v3.jsonl"))
    if not files:
        return None

    parent_events: list[dict] | None = None
    parent_id = ""
    call: dict | None = None
    for path in files:
        events = _events(path)
        if not events or events[0].get("type") != "session":
            continue
        for event in events:
            if event.get("type") == "tool/call" and _call_id(event) == call_id:
                parent_events = events
                parent_id = str(events[0].get("id") or "")
                call = event
                break
        if call is not None:
            break
    if call is None or parent_events is None:
        return {"missing": True}

    data = call.get("data") or {}
    name = str(data.get("name") or "")
    arguments = str(data.get("arguments") or "")
    output = ""
    failed = False
    ended = False
    for event in parent_events:
        if event.get("type") == "tool/result" and _call_id(event) == call_id:
            output, failed = _result_text(event)
            ended = True
            break

    steps: list[dict] = []
    if name == "subagent" and parent_id:
        label = _description(arguments)
        started = int(call.get("time") or 0)
        best: tuple[int, list[dict]] | None = None
        for path in files:
            events = _events(path)
            if not events or events[0].get("type") != "session":
                continue
            if str(events[0].get("parentSession") or "") != parent_id:
                continue
            if label and _child_label(events) != label:
                continue
            created = int(events[0].get("createdAt") or 0)
            distance = abs(created - started)
            if best is None or distance < best[0]:
                best = (distance, events)
        if best is not None:
            steps = _child_steps(best[1])

    return {
        "callId": call_id,
        "name": name,
        "title": _description(arguments) or name,
        "status": "failed" if failed else "done" if ended else "running",
        "input": _clip(arguments),
        "output": output,
        "steps": steps,
    }
