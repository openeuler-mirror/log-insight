"""个人知识库: 用户私有笔记 CRUD + 关键词/标签检索 (严格 owner 隔离)."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import NoteCreate, NoteInfo, NoteUpdate
from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.knowledge import KnowledgeNote
from app.models.user import User

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


async def _get_owned(db: AsyncSession, user: User, note_id: int) -> KnowledgeNote:
    """取本人笔记; 不存在或非本人一律 404 (不泄露存在性)."""
    note = (await db.execute(select(KnowledgeNote).where(
        KnowledgeNote.id == note_id, KnowledgeNote.owner_id == user.id))).scalar_one_or_none()
    if note is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "笔记不存在")
    return note


@router.get("", response_model=list[NoteInfo])
async def list_notes(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    q: str | None = None,
    tag: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """列出本人笔记: q 对标题/正文 ILIKE, tag 为 JSONB 标签包含."""
    conds = [KnowledgeNote.owner_id == user.id]
    if q:
        like = f"%{q}%"
        conds.append(or_(KnowledgeNote.title.ilike(like), KnowledgeNote.content.ilike(like)))
    if tag:
        conds.append(KnowledgeNote.tags.contains([tag]))
    query = (select(KnowledgeNote).where(*conds)
             .order_by(KnowledgeNote.updated_at.desc())
             .offset(max(offset, 0)).limit(min(max(limit, 1), 200)))
    return (await db.execute(query)).scalars().all()


@router.post("", response_model=NoteInfo, status_code=201)
async def create_note(
    body: NoteCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    """新建本人笔记."""
    note = KnowledgeNote(**body.model_dump(), owner_id=user.id)
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return note


@router.get("/{note_id}", response_model=NoteInfo)
async def get_note(
    note_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    """查看本人笔记详情."""
    return await _get_owned(db, user, note_id)


@router.put("/{note_id}", response_model=NoteInfo)
async def update_note(
    note_id: int,
    body: NoteUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    """整体更新本人笔记 (PUT 全量替换)."""
    note = await _get_owned(db, user, note_id)
    for key, value in body.model_dump().items():
        setattr(note, key, value)
    await db.commit()
    await db.refresh(note)
    return note


@router.delete("/{note_id}")
async def delete_note(
    note_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    """删除本人笔记."""
    note = await _get_owned(db, user, note_id)
    await db.delete(note)
    await db.commit()
    return {"detail": "已删除"}
