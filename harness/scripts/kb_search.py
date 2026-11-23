#!/usr/bin/env python3
"""用调用方给出的检索词查询本会话已选知识库，结果写入 kb_context.md。"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
VENV_PY = BACKEND / ".venv" / "bin" / "python"
if VENV_PY.is_file() and Path(sys.executable).resolve() != VENV_PY.resolve():
    os.execv(VENV_PY, [str(VENV_PY), *sys.argv])

SESSION_ID = ""
cwd = Path.cwd().resolve()
if cwd.parent.name == "diagnosis":
    SESSION_ID = cwd.name

sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)


def main() -> int:
    queries = [arg.strip() for arg in sys.argv[1:] if arg.strip()]
    if not SESSION_ID:
        print("请在诊断工作区中运行", file=sys.stderr)
        return 2
    try:
        uuid.UUID(SESSION_ID)
    except ValueError:
        print("诊断会话无效", file=sys.stderr)
        return 2
    if not queries:
        print("请传入从日志错误中整理出的检索词", file=sys.stderr)
        return 2

    async def run() -> int:
        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.models.diagnosis import DiagnosisSession
        from app.modules.kb.retrieve import search_with_queries

        async with AsyncSessionLocal() as db:
            sess = (await db.execute(
                select(DiagnosisSession).where(DiagnosisSession.id == uuid.UUID(SESSION_ID))
            )).scalar_one_or_none()
            if sess is None:
                print("诊断会话不存在", file=sys.stderr)
                return 2
            result = await search_with_queries(
                db,
                team_id=int(sess.team_id or 0),
                session_id=SESSION_ID,
                kb_ids=list(sess.selected_kb_ids or []),
                queries=queries,
                top_k=int(sess.kb_top_k or 10),
            )
        if result.note and not result.citations:
            print(result.note)
            return 0
        print(f"命中 {len(result.citations)} 条，已写入 kb_context.md")
        for item in result.citations:
            print(f"[{item.index}] {item.title}")
        return 0

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
