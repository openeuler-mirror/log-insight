"""审计日志写入辅助: 各 API 写操作调用, 统一记录操作人与团队."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog
from app.models.user import User


def log_audit(
    db: AsyncSession,
    user: User | None,
    action: str,
    target: str,
    team_id: int | None = None,
    success: bool = True,
    detail: dict | None = None,
) -> None:
    """写一条审计记录 (随调用方事务提交).

    同步函数: 仅做 db.add(), 无 IO/await; 由调用方后续 await db.commit() 落库。
    注意: 若改回 async, 必须同步为全部调用点补 await, 否则协程不会执行、审计丢失。

    user 为 None 时 (如登录失败), user_id 为空、user_name 传外部提供的名字.
    """
    db.add(AuditLog(
        user_id=user.id if user else None,
        user_name=user.display_name or user.username if user else (detail or {}).get("user_name", ""),
        action=action,
        team_id=team_id,
        target=target[:255],
        success=success,
        detail=detail or {},
    ))
