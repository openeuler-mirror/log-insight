"""团队知识库 API: CRUD / 上传 / 检索试跑."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import ensure_team_perm, get_current_user
from app.models.kb import KbDocument, KbFolder, KbIngestBatch, KbIngestItem, KnowledgeBase
from app.models.user import User
from app.modules.kb.ingest import ingest_document
from app.modules.kb.parsers import SUPPORTED, parse_file
from app.modules.kb.search import search_kbs

router = APIRouter(prefix="/teams/{team_id}/knowledge-bases", tags=["knowledge-base"])

# 模板化批次单批上限 (控制 LLM 转换成本)
_MAX_BATCH_FILES = 20


class KbCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    vector_enabled: bool = False
    embedding_config: dict[str, Any] = Field(default_factory=dict)
    case_template: str = ""


class KbUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    case_template: str | None = None
    status: str | None = None
    # Embedding: 模型锁定不可改；Key / 接口地址可轮换
    embedding_api_key: str | None = None
    embedding_api_base: str | None = None


class KbSearchBody(BaseModel):
    kb_ids: list[int]
    queries: list[str]
    top_k: int | None = None
    mode: str = "auto"


def _public_embedding_config(cfg: dict | None) -> dict:
    """对外不回传明文 Key，只暴露模型/地址/是否已配置密钥."""
    cfg = cfg or {}
    has_key = bool(cfg.get("api_key_enc") or cfg.get("api_key"))
    return {
        "model": str(cfg.get("model") or ""),
        "api_base": str(cfg.get("api_base") or ""),
        "provider": str(cfg.get("provider") or ""),
        "has_key": has_key,
        "key_hint": str(cfg.get("key_hint") or ("****" if has_key else "")),
    }


def _normalize_embedding_config(raw: dict | None) -> dict:
    """建库时规范化并加密 Embedding Key（可与团队 LLM Key 不同）."""
    from app.core.crypto import encrypt_secret
    raw = dict(raw or {})
    model = str(raw.get("model") or "").strip()
    api_base = str(raw.get("api_base") or "").strip().rstrip("/")
    provider = str(raw.get("provider") or "").strip()
    api_key = str(raw.get("api_key") or "").strip()
    if not model:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "启用向量时必须提供 Embedding 模型 ID")
    if not api_key:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "启用向量时必须提供 Embedding API Key")
    if len(api_key) > 512:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "API Key 过长")
    if api_base and not api_base.startswith(("http://", "https://")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Embedding 接口地址必须以 http:// 或 https:// 开头")
    out: dict[str, Any] = {
        "model": model,
        "api_base": api_base,
        "provider": provider,
        "api_key_enc": encrypt_secret(api_key),
        "key_hint": "****" + api_key[-4:],
    }
    return out


def _kb_info(kb: KnowledgeBase) -> dict:
    return {
        "id": kb.id,
        "team_id": kb.team_id,
        "name": kb.name,
        "description": kb.description,
        "case_template": kb.case_template,
        "vector_enabled": kb.vector_enabled,
        "embedding_config": (
            _public_embedding_config(kb.embedding_config) if kb.vector_enabled else {}
        ),
        "status": kb.status,
        "created_at": kb.created_at,
        "updated_at": kb.updated_at,
        # 明示: 无向量时前端展示徽章/说明
        "vector_hint": None if kb.vector_enabled else "本库未启用向量，仅支持关键词与 Grep，不支持语义检索",
    }


def _doc_info(d: KbDocument) -> dict:
    return {
        "id": d.id,
        "kb_id": d.kb_id,
        "title": d.title,
        "doc_type": d.doc_type,
        "ingest_mode": d.ingest_mode,
        "source_format": d.source_format,
        "rel_path": d.rel_path or "",
        "status": d.status,
        "enabled": d.enabled,
        "metadata": d.metadata_ or {},
        "created_at": d.created_at,
        "updated_at": d.updated_at,
    }


async def _get_kb(db: AsyncSession, team_id: int, kb_id: int) -> KnowledgeBase:
    kb = await db.get(KnowledgeBase, kb_id)
    if kb is None or kb.team_id != team_id or kb.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "知识库不存在")
    return kb


@router.get("")
async def list_kbs(
    team_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await ensure_team_perm(db, user, team_id, "kb:view")
    rows = (await db.execute(
        select(KnowledgeBase).where(
            KnowledgeBase.team_id == team_id,
            KnowledgeBase.deleted_at.is_(None),
        ).order_by(KnowledgeBase.id.desc())
    )).scalars().all()
    return [_kb_info(k) for k in rows]


@router.post("")
async def create_kb(
    team_id: int,
    body: KbCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await ensure_team_perm(db, user, team_id, "kb:manage")
    name = body.name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "名称不能为空")
    embed_cfg: dict = {}
    if body.vector_enabled:
        embed_cfg = _normalize_embedding_config(body.embedding_config)
    dup = (await db.execute(
        select(KnowledgeBase).where(
            KnowledgeBase.team_id == team_id,
            KnowledgeBase.name == name,
            KnowledgeBase.deleted_at.is_(None),
        )
    )).scalar_one_or_none()
    if dup:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "同名知识库已存在")

    kb = KnowledgeBase(
        team_id=team_id, name=name, description=body.description.strip(),
        case_template=body.case_template,
        vector_enabled=body.vector_enabled,
        embedding_config=embed_cfg,
        created_by=user.id,
    )
    db.add(kb)
    await db.commit()
    await db.refresh(kb)
    return _kb_info(kb)


@router.get("/{kb_id}")
async def get_kb(
    team_id: int, kb_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await ensure_team_perm(db, user, team_id, "kb:view")
    return _kb_info(await _get_kb(db, team_id, kb_id))


@router.patch("/{kb_id}")
async def update_kb(
    team_id: int, kb_id: int, body: KbUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await ensure_team_perm(db, user, team_id, "kb:manage")
    kb = await _get_kb(db, team_id, kb_id)
    if body.name is not None:
        kb.name = body.name.strip() or kb.name
    if body.description is not None:
        kb.description = body.description
    if body.case_template is not None:
        kb.case_template = body.case_template
    if body.status is not None:
        kb.status = body.status
    if body.embedding_api_key is not None or body.embedding_api_base is not None:
        if not kb.vector_enabled:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "仅关键词库无 Embedding 凭据可改")
        from app.core.crypto import encrypt_secret
        cfg = dict(kb.embedding_config or {})
        if body.embedding_api_base is not None:
            api_base = body.embedding_api_base.strip().rstrip("/")
            if api_base and not api_base.startswith(("http://", "https://")):
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "Embedding 接口地址必须以 http:// 或 https:// 开头",
                )
            cfg["api_base"] = api_base
        if body.embedding_api_key is not None:
            raw_key = body.embedding_api_key.strip()
            if raw_key:
                if len(raw_key) > 512:
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, "API Key 过长")
                cfg["api_key_enc"] = encrypt_secret(raw_key)
                cfg["key_hint"] = "****" + raw_key[-4:]
                cfg.pop("api_key", None)
        # 禁止通过本接口改 model / provider / vector_enabled
        kb.embedding_config = cfg
    await db.commit()
    await db.refresh(kb)
    return _kb_info(kb)


@router.delete("/{kb_id}")
async def delete_kb(
    team_id: int, kb_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await ensure_team_perm(db, user, team_id, "kb:manage")
    kb = await _get_kb(db, team_id, kb_id)
    kb.deleted_at = datetime.now(timezone.utc)
    await db.commit()
    return {"ok": True}


@router.get("/{kb_id}/documents")
async def list_docs(
    team_id: int, kb_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await ensure_team_perm(db, user, team_id, "kb:view")
    await _get_kb(db, team_id, kb_id)
    rows = (await db.execute(
        select(KbDocument).where(
            KbDocument.kb_id == kb_id,
            KbDocument.deleted_at.is_(None),
        ).order_by(KbDocument.id.desc())
    )).scalars().all()
    return [_doc_info(d) for d in rows]


@router.get("/{kb_id}/documents/{doc_id}")
async def get_doc(
    team_id: int, kb_id: int, doc_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """文档详情含正文, 供知识库详情页阅读."""
    await ensure_team_perm(db, user, team_id, "kb:view")
    await _get_kb(db, team_id, kb_id)
    doc = await db.get(KbDocument, doc_id)
    if doc is None or doc.kb_id != kb_id or doc.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文档不存在")
    info = _doc_info(doc)
    info["content"] = doc.content_norm or ""
    return info


@router.delete("/{kb_id}/documents/{doc_id}")
async def delete_doc(
    team_id: int, kb_id: int, doc_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await ensure_team_perm(db, user, team_id, "kb:manage")
    await _get_kb(db, team_id, kb_id)
    doc = await db.get(KbDocument, doc_id)
    if doc is None or doc.kb_id != kb_id or doc.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文档不存在")
    now = datetime.now(timezone.utc)
    doc.deleted_at = now
    from app.models.kb import KbChunk
    chunks = (await db.execute(select(KbChunk).where(KbChunk.document_id == doc.id))).scalars().all()
    for c in chunks:
        c.deleted_at = now
    await db.commit()
    _purge_storage(doc.storage_path)  # 软删后回收磁盘原件
    return {"ok": True}


class FolderCreate(BaseModel):
    path: str = Field(min_length=1, max_length=512)


class FolderRename(BaseModel):
    path: str = Field(min_length=1, max_length=512)
    name: str = Field(min_length=1, max_length=128)


def _norm_folder_path(raw: str) -> str:
    """规范化目录路径: 统一分隔符, 去掉空段与 '.'; 拒绝 '..'."""
    parts = [p for p in raw.replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "目录路径不合法")
    path = "/".join(parts)
    if not path or len(path) > 512:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "目录路径不合法")
    return path


def _like_prefix(col, prefix: str):
    """前缀 LIKE, 转义 %/_: 目录名含通配符时不能波及同形路径 (如 a_b 误伤 axb)."""
    esc = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return col.like(esc + "/%", escape="\\")


def _norm_rel_path(raw: str) -> str:
    """清洗上传相对路径: 统一分隔符, 丢弃空段与 '.', 拒绝 '..' (与目录接口一致)."""
    parts = raw.strip().replace("\\", "/").split("/")
    if ".." in parts:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "相对路径不允许包含 '..'")
    return "/".join(p for p in parts if p not in ("", "."))[:512]


def _purge_storage(*paths: str) -> None:
    """尽力清理磁盘原件, 失败不阻断删除流程."""
    for p in filter(None, paths):
        try:
            Path(p).unlink(missing_ok=True)
        except OSError:
            pass


def _folder_info(f: KbFolder) -> dict:
    return {
        "id": f.id,
        "kb_id": f.kb_id,
        "path": f.path,
        "created_at": f.created_at,
        "updated_at": f.updated_at,
    }


@router.get("/{kb_id}/folders")
async def list_folders(
    team_id: int, kb_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await ensure_team_perm(db, user, team_id, "kb:view")
    await _get_kb(db, team_id, kb_id)
    rows = (await db.execute(
        select(KbFolder).where(KbFolder.kb_id == kb_id).order_by(KbFolder.path)
    )).scalars().all()
    return [_folder_info(f) for f in rows]


@router.post("/{kb_id}/folders")
async def create_folder(
    team_id: int, kb_id: int, body: FolderCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await ensure_team_perm(db, user, team_id, "kb:manage")
    kb = await _get_kb(db, team_id, kb_id)
    path = _norm_folder_path(body.path)
    dup = (await db.execute(
        select(KbFolder).where(KbFolder.kb_id == kb_id, KbFolder.path == path)
    )).scalar_one_or_none()
    if dup:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "同名目录已存在")
    f = KbFolder(kb_id=kb.id, team_id=team_id, path=path)
    db.add(f)
    await db.commit()
    await db.refresh(f)
    return _folder_info(f)


@router.patch("/{kb_id}/folders")
async def rename_folder(
    team_id: int, kb_id: int, body: FolderRename,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await ensure_team_perm(db, user, team_id, "kb:manage")
    await _get_kb(db, team_id, kb_id)
    old = _norm_folder_path(body.path)
    name = body.name.strip()
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "目录名不合法")
    parent = old.rsplit("/", 1)[0] if "/" in old else ""
    new = f"{parent}/{name}" if parent else name
    if new == old:
        return {"ok": True, "path": new}
    # 目标路径已被显式目录或文档占用时拒绝合并
    clash = (await db.execute(
        select(KbFolder.id).where(
            KbFolder.kb_id == kb_id,
            (KbFolder.path == new) | _like_prefix(KbFolder.path, new),
        ).limit(1)
    )).scalar_one_or_none()
    if clash:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "目标目录已存在")
    doc_clash = (await db.execute(
        select(KbDocument.id).where(
            KbDocument.kb_id == kb_id,
            KbDocument.deleted_at.is_(None),
            (KbDocument.rel_path == new) | _like_prefix(KbDocument.rel_path, new),
        ).limit(1)
    )).scalar_one_or_none()
    if doc_clash:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "目标目录下已有文档")
    # 批量 UPDATE 一次性改写前缀; 并发冲突由 (kb_id, path) 唯一约束兜底
    try:
        await db.execute(
            update(KbFolder)
            .where(KbFolder.kb_id == kb_id, (KbFolder.path == old) | _like_prefix(KbFolder.path, old))
            .values(path=func.concat(new, func.substr(KbFolder.path, len(old) + 1))),
            execution_options={"synchronize_session": False},
        )
        await db.execute(
            update(KbDocument)
            .where(KbDocument.kb_id == kb_id, KbDocument.deleted_at.is_(None),
                   _like_prefix(KbDocument.rel_path, old))
            .values(rel_path=func.concat(new, func.substr(KbDocument.rel_path, len(old) + 1))),
            execution_options={"synchronize_session": False},
        )
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "目标目录已存在 (并发修改, 请重试)")
    await db.commit()
    return {"ok": True, "path": new}


@router.delete("/{kb_id}/folders")
async def delete_folder(
    team_id: int, kb_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    path: str = Query(..., min_length=1),
):
    await ensure_team_perm(db, user, team_id, "kb:manage")
    await _get_kb(db, team_id, kb_id)
    old = _norm_folder_path(path)
    now = datetime.now(timezone.utc)
    docs = (await db.execute(
        select(KbDocument).where(
            KbDocument.kb_id == kb_id,
            KbDocument.deleted_at.is_(None),
            _like_prefix(KbDocument.rel_path, old),
        )
    )).scalars().all()
    from app.models.kb import KbChunk
    for d in docs:
        d.deleted_at = now
    if docs:
        chunks = (await db.execute(
            select(KbChunk).where(KbChunk.document_id.in_([d.id for d in docs]))
        )).scalars().all()
        for c in chunks:
            c.deleted_at = now
    folders = (await db.execute(
        select(KbFolder).where(
            KbFolder.kb_id == kb_id,
            (KbFolder.path == old) | _like_prefix(KbFolder.path, old),
        )
    )).scalars().all()
    for f in folders:
        await db.delete(f)
    await db.commit()
    _purge_storage(*(d.storage_path or "" for d in docs))  # 软删后回收磁盘原件
    return {"ok": True, "deleted_docs": len(docs)}


def _item_info(it: KbIngestItem) -> dict:
    return {
        "id": it.id,
        "batch_id": it.batch_id,
        "filename": it.filename,
        "rel_path": it.rel_path or "",
        "parse_status": it.parse_status,
        "convert_status": it.convert_status,
        "preview_markdown": it.preview_markdown,
        "error_message": it.error_message,
        "selected": it.selected,
        "document_id": it.document_id,
    }


def _batch_info(batch: KbIngestBatch, items: list[KbIngestItem]) -> dict:
    return {
        "id": batch.id,
        "team_id": batch.team_id,
        "kb_id": batch.kb_id,
        "mode": batch.mode,
        "status": batch.status,
        "created_at": batch.created_at,
        "items": [_item_info(i) for i in items],
    }


async def _team_chat(db: AsyncSession, team_id: int) -> dict | None:
    from app.core.crypto import decrypt_secret
    from app.core.config import get_settings as _gs
    from app.modules.diagnosis.team_models import default_model_row
    found = await default_model_row(db, team_id)
    if found is None:
        return None
    mrow, prow = found
    key = decrypt_secret(prow.api_key_enc) if prow.api_key_enc else ""
    if not key and prow.provider == "deepseek":
        key = _gs().AGENT_API_KEY or ""
    if not key:
        return None
    return {
        "api_base": prow.api_base or "",
        "api_key": key,
        "model": mrow.model_id,
        "protocol": prow.protocol or "messages",
    }


@router.post("/{kb_id}/upload")
async def upload_raw(
    team_id: int, kb_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...),
    mode: str = Form("raw"),
    rel_path: str = Form(""),
):
    """原样上传并同步入库. 模板化请用 upload-batch."""
    await ensure_team_perm(db, user, team_id, "kb:manage")
    if mode != "raw":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "模板化请使用多文件批次上传接口")
    kb = await _get_kb(db, team_id, kb_id)
    settings = get_settings()
    data = await file.read()
    max_b = settings.KB_MAX_UPLOAD_MB * 1024 * 1024
    if len(data) > max_b:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"文件超过 {settings.KB_MAX_UPLOAD_MB}MB")
    name = file.filename or "upload.bin"
    ext = Path(name).suffix.lower()
    if ext not in SUPPORTED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"不支持的格式: {ext}")
    rel = _norm_rel_path(rel_path)  # 校验在写盘前, 坏路径不留磁盘孤儿文件

    root = Path(settings.DATA_DIR) / "kb" / str(team_id) / str(kb_id)
    root.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.\-]+", "_", Path(name).name)[:180]
    dest = root / f"{uuid.uuid4().hex[:10]}_{safe}"
    dest.write_bytes(data)

    doc = KbDocument(
        kb_id=kb.id, team_id=team_id,
        title=Path(name).stem[:256],
        doc_type="raw_import", ingest_mode="raw",
        source_format=ext.lstrip("."),
        storage_path=str(dest),
        rel_path=rel,
        status="pending", created_by=user.id,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    await ingest_document(db, doc.id)
    await db.refresh(doc)
    return _doc_info(doc)


@router.post("/{kb_id}/upload-batch")
async def upload_batch(
    team_id: int, kb_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    files: list[UploadFile] = File(...),
    mode: str = Form("template"),
    rel_paths: str = Form("[]"),
):
    """模板化多文件上传: 解析 → LLM 转换预览 → 返回批次供勾选导入.

    rel_paths: JSON 数组, 与 files 顺序对齐, 用于保留目录层级.
    """
    await ensure_team_perm(db, user, team_id, "kb:manage")
    if mode != "template":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "upload-batch 仅支持 mode=template")
    if not files:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请至少上传一个文件")
    if len(files) > _MAX_BATCH_FILES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"单批最多 {_MAX_BATCH_FILES} 个文件, 当前 {len(files)} 个, 请分批上传")
    kb = await _get_kb(db, team_id, kb_id)
    settings = get_settings()
    max_b = settings.KB_MAX_UPLOAD_MB * 1024 * 1024
    chat = await _team_chat(db, team_id)
    # 目录上传时前端按 files 顺序传相对路径; 格式错误直接拒绝而非静默丢层级
    try:
        rel_list = json.loads(rel_paths) if rel_paths else []
    except (TypeError, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "rel_paths 不是合法 JSON")
    if not isinstance(rel_list, list):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "rel_paths 必须为字符串数组")
    rel_list = [_norm_rel_path(str(x)) for x in rel_list]

    batch = KbIngestBatch(
        team_id=team_id, kb_id=kb.id, mode="template",
        status="converting", created_by=user.id,
    )
    db.add(batch)
    await db.flush()

    root = Path(settings.DATA_DIR) / "kb" / str(team_id) / str(kb_id) / "batches" / str(batch.id)
    root.mkdir(parents=True, exist_ok=True)
    items: list[KbIngestItem] = []

    from app.modules.kb.case_convert import convert_case

    for idx, f in enumerate(files):
        name = f.filename or "upload.bin"
        ext = Path(name).suffix.lower()
        data = await f.read()
        item = KbIngestItem(
            batch_id=batch.id, filename=Path(name).name[:256],
            rel_path=rel_list[idx] if idx < len(rel_list) else "",
        )
        if ext not in SUPPORTED:
            item.parse_status = "failed"
            item.convert_status = "failed"
            item.error_message = f"不支持的格式: {ext}"
        elif len(data) > max_b:
            item.parse_status = "failed"
            item.convert_status = "failed"
            item.error_message = f"文件超过 {settings.KB_MAX_UPLOAD_MB}MB"
        else:
            safe = re.sub(r"[^\w.\-]+", "_", Path(name).name)[:180]
            dest = root / f"{uuid.uuid4().hex[:8]}_{safe}"
            dest.write_bytes(data)
            item.storage_path = str(dest)
            try:
                text = parse_file(dest)
                item.parse_status = "ok"
                try:
                    preview = await convert_case(
                        content=text,
                        title=Path(name).stem,
                        template=kb.case_template or "",
                        chat=chat,
                    )
                    item.convert_status = "ok"
                    item.preview_markdown = preview
                    item.selected = True
                except Exception as exc:  # noqa: BLE001
                    item.convert_status = "failed"
                    item.error_message = str(exc)[:500]
            except Exception as exc:  # noqa: BLE001
                item.parse_status = "failed"
                item.convert_status = "failed"
                item.error_message = f"解析失败: {exc}"[:500]
        db.add(item)
        items.append(item)

    batch.status = "ready"
    await db.commit()
    for it in items:
        await db.refresh(it)
    await db.refresh(batch)
    return _batch_info(batch, items)


class BatchCommit(BaseModel):
    item_ids: list[int]


search_router = APIRouter(prefix="/teams/{team_id}/kb", tags=["knowledge-base"])


@search_router.get("/batches/{batch_id}")
async def get_batch(
    team_id: int, batch_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await ensure_team_perm(db, user, team_id, "kb:manage")
    batch = await db.get(KbIngestBatch, batch_id)
    if batch is None or batch.team_id != team_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "批次不存在")
    items = list((await db.execute(
        select(KbIngestItem).where(KbIngestItem.batch_id == batch.id)
        .order_by(KbIngestItem.id.asc())
    )).scalars().all())
    return _batch_info(batch, items)


@search_router.post("/batches/{batch_id}/commit")
async def commit_batch(
    team_id: int, batch_id: int, body: BatchCommit,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await ensure_team_perm(db, user, team_id, "kb:manage")
    batch = await db.get(KbIngestBatch, batch_id)
    if batch is None or batch.team_id != team_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "批次不存在")
    kb = await _get_kb(db, team_id, batch.kb_id)
    ids = list(dict.fromkeys(body.item_ids))
    if not ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请至少勾选一项")
    items = list((await db.execute(
        select(KbIngestItem).where(
            KbIngestItem.batch_id == batch.id,
            KbIngestItem.id.in_(ids),
        )
    )).scalars().all())
    docs = []
    for it in items:
        if it.convert_status != "ok" or not it.preview_markdown.strip():
            continue
        title = Path(it.filename).stem[:256] or f"case-{it.id}"
        doc = KbDocument(
            kb_id=kb.id, team_id=team_id,
            title=title,
            doc_type="converted_case", ingest_mode="template",
            source_format=Path(it.filename).suffix.lstrip(".") or "md",
            storage_path=it.storage_path,
            rel_path=it.rel_path or "",
            content_norm=it.preview_markdown,
            status="pending", created_by=user.id,
        )
        db.add(doc)
        await db.flush()
        it.selected = True
        it.document_id = doc.id
        docs.append(doc)
    batch.status = "committed"
    await db.commit()
    for doc in docs:
        await ingest_document(db, doc.id)
    refreshed = []
    for doc in docs:
        await db.refresh(doc)
        refreshed.append(_doc_info(doc))
    return {"ok": True, "documents": refreshed, "count": len(refreshed)}


@search_router.post("/search")
async def kb_search(
    team_id: int, body: KbSearchBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await ensure_team_perm(db, user, team_id, "kb:search")
    hits = await search_kbs(
        db, team_id=team_id, kb_ids=body.kb_ids,
        queries=body.queries, top_k=body.top_k, mode=body.mode,
    )
    doc_ids = {h.document_id for h in hits}
    titles = {}
    if doc_ids:
        docs = (await db.execute(select(KbDocument).where(KbDocument.id.in_(doc_ids)))).scalars().all()
        titles = {d.id: d.title for d in docs}
    return [
        {
            "chunk_id": h.chunk_id,
            "document_id": h.document_id,
            "kb_id": h.kb_id,
            "kb_name": h.kb_name,
            "title": titles.get(h.document_id, ""),
            "content": h.content,
            "score": h.score,
        }
        for h in hits
    ]
