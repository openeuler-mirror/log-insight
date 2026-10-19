"""本地文件系统浏览: 目录树 / 单层列表 / 文件内容 / 原始二进制 / 变更监听(SSE).

供「智能诊断」左栏「目录文件」使用:
- 加载策略: 默认一次性嵌套加载(`/fs/tree`); 仅当某目录子项超过 FS_EAGER_MAX_CHILDREN,
  或超出深度/节点预算时, 该目录不带 children(前端展开时再调 `/fs/list` 懒加载);
- 实时刷新: `/fs/watch` 用 watchdog 递归监听所选目录, 变化经 SSE 推送前端;
- 安全: 仅允许访问 FS_ROOTS 白名单内的绝对路径。
"""
import asyncio
import json
import mimetypes
import os
import re
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app.core.config import get_settings
from app.core.deps import get_current_user, get_current_user_or_query
from app.models.user import User

router = APIRouter(prefix="/fs", tags=["fs"])
settings = get_settings()

_IMG_EXT = re.compile(r"\.(png|jpe?g|gif|svg|webp|bmp|ico)$", re.I)
_EXT_LANG: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\.json$", re.I), "json"),
    (re.compile(r"\.(ini|conf|cfg|properties)$", re.I), "ini"),
    (re.compile(r"\.ya?ml$", re.I), "yaml"),
    (re.compile(r"\.(sh|bash)$", re.I), "bash"),
    (re.compile(r"\.py$", re.I), "python"),
    (re.compile(r"\.(ts|tsx|js|jsx|vue)$", re.I), "ts"),
    (re.compile(r"\.md$", re.I), "markdown"),
    (re.compile(r"\.(xml|html?)$", re.I), "html"),
]


def _lang_of(name: str) -> str:
    for rex, lang in _EXT_LANG:
        if rex.search(name):
            return lang
    return "text"


def _default_roots() -> list[Path]:
    """默认白名单: 用户主目录 + Windows 盘符."""
    roots = [Path.home().resolve()]
    if os.name == "nt":
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            drive = Path(f"{letter}:/")
            if drive.exists() and drive not in roots:
                roots.append(drive)
    return roots


@lru_cache
def _roots() -> list[Path]:
    raw = (settings.FS_ROOTS or "").strip()
    if raw:
        return [Path(seg.strip()).expanduser().resolve() for seg in raw.split(",") if seg.strip()]
    return _default_roots()


def _resolve(path: str) -> Path:
    """绝对路径 + 白名单校验, 不通过抛 403."""
    if not path:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "缺少 path 参数")
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "必须使用绝对路径")
    try:
        rp = p.resolve()
    except OSError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"路径无效: {e}") from e
    for root in _roots():
        try:
            rp.relative_to(root)
            return rp
        except ValueError:
            continue
    raise HTTPException(status.HTTP_403_FORBIDDEN, "路径不在允许访问的范围内")


def _read_dir(d: Path) -> list[dict[str, Any]]:
    """读取单层目录项: 目录优先 + 名称升序; 无权限 / 不存在返回空."""
    try:
        with os.scandir(d) as it:
            entries = list(it)
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for e in entries:
        try:
            is_dir = e.is_dir(follow_symlinks=False)
            st = e.stat(follow_symlinks=False)
        except OSError:
            continue
        out.append({
            "name": e.name,
            "path": e.path,
            "kind": "dir" if is_dir else "file",
            "size": 0 if is_dir else st.st_size,
            "mtime": st.st_mtime,
        })
    out.sort(key=lambda x: (x["kind"] != "dir", x["name"].lower()))
    return out


def _build_tree(root: Path, depth: int, max_nodes: int, max_children: int) -> tuple[dict[str, Any], bool]:
    """递归构建嵌套树, 返回 (根节点, 是否被截断).

    带 children 的目录 = 已加载(可能为空数组); 不带 children = 未加载, 前端展开时懒加载。
    """
    counter = 0
    truncated = False

    def walk(p: Path, level: int) -> dict[str, Any]:
        nonlocal counter, truncated
        counter += 1
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0.0
        node: dict[str, Any] = {"name": p.name or str(p), "path": str(p), "kind": "dir", "size": 0, "mtime": mtime}
        if level >= depth or counter >= max_nodes:
            truncated = True
            return node
        entries = _read_dir(p)
        if len(entries) > max_children:
            return node  # 子项过多 → 交由前端懒加载
        children: list[dict[str, Any]] = []
        for e in entries:
            if e["kind"] == "file":
                counter += 1
                children.append(e)
                continue
            if counter >= max_nodes:
                children.append(e)
                truncated = True
                continue
            children.append(walk(Path(e["path"]), level + 1))
        node["children"] = children
        return node

    return walk(root, 0), truncated


@router.get("/roots")
def list_roots(user: Annotated[User, Depends(get_current_user)]):
    """可浏览的根目录列表(默认用户主目录 + Windows 盘符)."""
    roots = [{"name": r.name or str(r), "path": str(r)} for r in _roots() if r.exists()]
    return {"roots": roots, "home": str(Path.home())}


@router.get("/list")
def list_dir(path: str, user: Annotated[User, Depends(get_current_user)]):
    """单层目录列表(懒加载用): 目录优先 + 名称升序."""
    d = _resolve(path)
    if not d.is_dir():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "目录不存在或不可访问")
    entries = _read_dir(d)
    limit = settings.FS_LIST_MAX
    return {"path": str(d), "entries": entries[:limit], "truncated": len(entries) > limit}


@router.get("/tree")
def read_tree(
    path: str,
    user: Annotated[User, Depends(get_current_user)],
    depth: int | None = None,
):
    """一次性嵌套加载目录树; 超出阈值/预算的目录不带 children, 由前端懒加载."""
    root = _resolve(path)
    if not root.is_dir():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "目录不存在或不可访问")
    d = settings.FS_TREE_MAX_DEPTH if depth is None else max(1, min(depth, 16))
    node, truncated = _build_tree(root, d, settings.FS_TREE_MAX_NODES, settings.FS_EAGER_MAX_CHILDREN)
    return {"root": node, "truncated": truncated}


@router.get("/read")
def read_file(path: str, user: Annotated[User, Depends(get_current_user)]):
    """读取文件内容: 文本直接返回, 图片仅返回元信息(前端走 /fs/raw 取二进制)."""
    f = _resolve(path)
    if not f.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文件不存在或不可访问")
    try:
        st = f.stat()
    except OSError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"文件不可访问: {e}") from e
    meta = {"path": str(f), "size": st.st_size, "mtime": st.st_mtime}
    if _IMG_EXT.search(f.name):
        return {"kind": "image", **meta}

    max_bytes = int(settings.FS_MAX_TEXT_MB * 1024 * 1024)
    if st.st_size > max_bytes:
        return {"kind": "text", "lang": _lang_of(f.name), **meta,
                "content": f"# {f.name} ({st.st_size / 1024 / 1024:.1f} MB)\n"
                           f"# 文件超过 {settings.FS_MAX_TEXT_MB} MB, 未加载内容。\n"}
    try:
        raw = f.read_bytes()
    except OSError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"读取失败: {e}") from e
    if b"\x00" in raw[:4096]:
        return {"kind": "text", "lang": "text", **meta,
                "content": f"# {f.name} ({st.st_size / 1024:.1f} KB)\n# 二进制文件, 未加载内容。\n"}
    return {"kind": "text", "lang": _lang_of(f.name), "content": raw.decode("utf-8", errors="replace"), **meta}


@router.get("/raw")
def read_raw(path: str, user: Annotated[User, Depends(get_current_user_or_query)]):
    """原始二进制(图片预览): EventSource / <img> 无法带 Header, 支持 ?token= 认证."""
    f = _resolve(path)
    if not f.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文件不存在或不可访问")
    media, _ = mimetypes.guess_type(f.name)
    return FileResponse(f, media_type=media or "application/octet-stream",
                        headers={"Content-Disposition": "inline"})


class _WatchHandler(FileSystemEventHandler):
    """watchdog 回调(运行在监听线程): 去重后经事件循环投递到 asyncio 队列."""

    def __init__(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue, debounce: float = 0.4):
        self._loop = loop
        self._queue = queue
        self._debounce = debounce
        self._recent: dict[tuple, float] = {}
        self._lock = threading.Lock()

    def _emit(self, op: str, path: str, is_dir: bool, src: str | None = None) -> None:
        key = (op, path, is_dir)
        now = time.monotonic()
        with self._lock:
            last = self._recent.get(key)
            if last is not None and now - last < self._debounce:
                return
            self._recent[key] = now
            if len(self._recent) > 512:  # 防止字典无限增长
                cutoff = now - 5.0
                self._recent = {k: v for k, v in self._recent.items() if v >= cutoff}
        payload = {"op": op, "path": path, "kind": "dir" if is_dir else "file",
                   "dir": str(Path(path).parent)}
        if src is not None:
            payload["src"] = src
            payload["src_dir"] = str(Path(src).parent)
        self._loop.call_soon_threadsafe(self._queue.put_nowait, payload)

    def on_created(self, event: FileSystemEvent) -> None:
        self._emit("created", str(event.src_path), event.is_directory)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._emit("modified", str(event.src_path), event.is_directory)

    def on_deleted(self, event: FileSystemEvent) -> None:
        self._emit("deleted", str(event.src_path), event.is_directory)

    def on_moved(self, event: FileSystemEvent) -> None:
        self._emit("moved", str(event.dest_path), event.is_directory, str(event.src_path))


@router.get("/watch")
async def watch_dir(
    path: str,
    request: Request,
    user: Annotated[User, Depends(get_current_user_or_query)],
):
    """SSE: 递归监听目录变化, 事件形如 {op, path, kind, dir} (+ src/src_dir for moved)."""
    root = _resolve(path)
    if not root.is_dir():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "目录不存在或不可访问")

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=2000)
    observer = Observer()
    observer.schedule(_WatchHandler(loop, queue), str(root), recursive=True)
    observer.start()

    async def event_generator():
        try:
            yield {"event": "ready", "data": json.dumps({"root": str(root)}, ensure_ascii=False)}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}  # 保活
                    continue
                yield {"event": "change", "data": json.dumps(payload, ensure_ascii=False)}
        finally:
            observer.stop()
            await asyncio.to_thread(observer.join, 2.0)

    return EventSourceResponse(event_generator())
