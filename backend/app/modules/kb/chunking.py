"""按字符窗口切块 (近似 token; 避免强依赖 tiktoken)."""
from __future__ import annotations


def split_chunks(text: str, size: int = 512, overlap: int = 64) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    # 按段落优先
    paras = [p.strip() for p in text.replace("\r\n", "\n").split("\n\n") if p.strip()]
    if not paras:
        paras = [text]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if len(p) > size:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.extend(_window(p, size, overlap))
            continue
        if len(buf) + len(p) + 2 <= size:
            buf = f"{buf}\n\n{p}" if buf else p
        else:
            if buf:
                chunks.append(buf)
            buf = p
    if buf:
        chunks.append(buf)
    return chunks


def _window(s: str, size: int, overlap: int) -> list[str]:
    out: list[str] = []
    step = max(1, size - overlap)
    i = 0
    while i < len(s):
        out.append(s[i : i + size])
        if i + size >= len(s):
            break
        i += step
    return out
