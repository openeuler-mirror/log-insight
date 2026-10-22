"""把个人级供应商凭据/模型选择迁移为团队配置 (默认 dry-run).

用法 (容器内):
    python -m app.modules.diagnosis.team_migration          # 只打印迁移计划
    python -m app.modules.diagnosis.team_migration --apply  # 执行迁移

规则:
- 仅迁移团队创建者 (owner) 的个人凭据, 避免把成员个人 Key 共享给团队;
- 团队成员的个人 Key 只列入报告, 由团队管理员重新录入;
- 已有团队凭据/同名模型不覆盖 (幂等).
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.diagnosis import (DiagnosisUserConfig, DiagnosisUserProvider,
                                  DiagnosisUserSelection)
from app.models.llm import TeamLlmModel, TeamLlmProvider
from app.models.team import Team
from app.modules.agent.providers import spec_of


async def build_plan(db) -> list[dict]:
    teams = list((await db.execute(select(Team).order_by(Team.id))).scalars().all())
    plan: list[dict] = []
    for team in teams:
        owner = team.owner_user_id
        providers = list((await db.execute(select(DiagnosisUserProvider).where(
            DiagnosisUserProvider.user_id == owner))).scalars().all())
        selections = list((await db.execute(select(DiagnosisUserSelection).where(
            DiagnosisUserSelection.user_id == owner))).scalars().all())
        config = await db.get(DiagnosisUserConfig, owner)
        existing = {row.provider for row in (await db.execute(
            select(TeamLlmProvider).where(TeamLlmProvider.team_id == team.id))).scalars().all()}
        entry = {
            "team": f"#{team.id} {team.name}",
            "owner_user_id": owner,
            "providers": [],
            "skipped_members": [],
        }
        for row in providers:
            if not row.api_key_enc:
                continue
            if row.provider in existing:
                entry["providers"].append({"provider": row.provider, "action": "skip-exists"})
                continue
            sel = next((s for s in selections if s.provider == row.provider), None)
            model = ""
            name = ""
            if config and config.provider == row.provider and config.model:
                model, name = config.model, config.model_name
            elif sel:
                model, name = sel.model, sel.model_name
            entry["providers"].append({
                "provider": row.provider,
                "label": spec_of(row.provider).label,
                "key_hint": row.key_hint,
                "api_base": row.api_base,
                "protocol": row.protocol,
                "model": model,
                "model_name": name,
                "action": "copy",
            })
        plan.append(entry)
    return plan


async def apply_plan(db, plan: list[dict]) -> None:
    teams = {f"#{t.id} {t.name}": t for t in
             (await db.execute(select(Team))).scalars().all()}
    for entry in plan:
        team = teams.get(entry["team"])
        if team is None:
            continue
        for item in entry["providers"]:
            if item["action"] != "copy":
                continue
            source = (await db.execute(select(DiagnosisUserProvider).where(
                DiagnosisUserProvider.user_id == entry["owner_user_id"],
                DiagnosisUserProvider.provider == item["provider"]))).scalar_one_or_none()
            if source is None:
                continue
            prow = TeamLlmProvider(
                team_id=team.id, provider=source.provider, label=spec_of(source.provider).label,
                api_key_enc=source.api_key_enc, key_hint=source.key_hint,
                api_base=source.api_base, protocol=source.protocol,
                enabled=True, created_by=entry["owner_user_id"],
            )
            db.add(prow)
            await db.flush()
            if item["model"]:
                db.add(TeamLlmModel(
                    team_id=team.id, provider_id=prow.id, model_id=item["model"],
                    display_name=item["model_name"], enabled=True, is_default=True,
                ))
    await db.commit()


async def main(apply: bool) -> None:
    async with AsyncSessionLocal() as db:
        plan = await build_plan(db)
    for entry in plan:
        print(f"团队 {entry['team']} (owner={entry['owner_user_id']})")
        if not entry["providers"]:
            print("  - 无迁移项")
        for item in entry["providers"]:
            if item["action"] == "skip-exists":
                print(f"  - {item['provider']}: 已存在团队凭据, 跳过")
            else:
                print(f"  - {item['provider']} ({item['label']}): 复制凭据 {item.get('key_hint', '')}"
                      f" 模型={item.get('model') or '未选择'} 名称={item.get('model_name') or '-'}")
    if not apply:
        print("\n以上为 dry-run; 确认无误后加 --apply 执行。")
        return
    async with AsyncSessionLocal() as db:
        await apply_plan(db, plan)
    print("\n迁移完成。")


if __name__ == "__main__":
    asyncio.run(main("--apply" in sys.argv))
