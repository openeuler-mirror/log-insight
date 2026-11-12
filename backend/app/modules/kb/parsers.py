"""知识库文档解析: 按扩展名抽出纯文本 (一期不做 .doc)."""
from __future__ import annotations

import csv
import io
import re
from html.parser import HTMLParser
from pathlib import Path

SUPPORTED = {".txt", ".md", ".pdf", ".docx", ".xlsx", ".xls", ".csv", ".html", ".htm"}


def parse_file(path: Path) -> str:
    ext = path.suffix.lower()
    if ext not in SUPPORTED:
        raise ValueError(f"不支持的格式: {ext}")
    if ext in {".txt", ".md"}:
        raw = path.read_bytes()
        for enc in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")
    if ext == ".pdf":
        return _pdf(path)
    if ext == ".docx":
        return _docx(path)
    if ext in {".xlsx", ".xls"}:
        return _xlsx(path)
    if ext == ".csv":
        return _csv(path)
    if ext in {".html", ".htm"}:
        return _html(path)
    raise ValueError(f"不支持的格式: {ext}")


def _pdf(path: Path) -> str:
    try:
        import fitz  # PyMuPDF
    except ImportError as e:
        raise ValueError("解析 PDF 需要安装 pymupdf") from e
    doc = fitz.open(path)
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def _docx(path: Path) -> str:
    try:
        from docx import Document
    except ImportError as e:
        raise ValueError("解析 docx 需要安装 python-docx") from e
    d = Document(str(path))
    parts = [p.text for p in d.paragraphs if p.text.strip()]
    for table in d.tables:
        for row in table.rows:
            parts.append(" | ".join(c.text.strip() for c in row.cells))
    return "\n".join(parts)


def _xlsx(path: Path) -> str:
    try:
        import openpyxl
    except ImportError as e:
        raise ValueError("解析 Excel 需要安装 openpyxl") from e
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    lines: list[str] = []
    for sheet in wb.worksheets:
        lines.append(f"# {sheet.title}")
        for row in sheet.iter_rows(values_only=True):
            cells = ["" if c is None else str(c) for c in row]
            if any(x.strip() for x in cells):
                lines.append(" | ".join(cells))
    wb.close()
    return "\n".join(lines)


def _csv(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    return "\n".join(" | ".join(row) for row in reader)


class _HtmlTextExtractor(HTMLParser):
    """把 HTML 抽成纯文本: 跳过脚本/样式, 块级元素换行, 保留可读结构."""

    _SKIP = {"script", "style", "noscript", "template", "head", "iframe"}
    _BLOCK = {
        "p", "div", "br", "hr", "li", "tr", "td", "th", "pre", "blockquote",
        "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table",
        "ul", "ol", "dl", "dt", "dd", "body",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip += 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self._parts.append(data.strip() + " ")

    def text(self) -> str:
        raw = "".join(self._parts)
        lines = (re.sub(r"[ \t]+", " ", ln).strip() for ln in raw.splitlines())
        return "\n".join(ln for ln in lines if ln)


def _html(path: Path) -> str:
    raw = path.read_bytes()
    # utf-8-sig 优先: 带 BOM 的页面不会把 BOM 混进正文; latin-1 兜底永不失败
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    parser = _HtmlTextExtractor()
    parser.feed(text)
    parser.close()
    return parser.text()
