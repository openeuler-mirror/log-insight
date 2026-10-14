"""智能巡检规则推荐: 触发 → SSE 流式 → 持久化 → 覆盖去重 → 权限/前置校验 测试

    :Preparation
        1. 依赖本地真实基础设施: PostgreSQL + Redis(测试专用 db9); 连接由 backend/conftest.py 在导入 app 前注入;
        2. 以 `with TestClient(app)` 启动真实 lifespan(Redis/schema 守卫/幂等种子); 全部请求与辅助操作复用同一事件循环;
        3. 准备两个真实用户: 任务所有者 owner 与 无关用户 other, 令牌由 create_access_token 直接签发;
        4. 准备一份可解析的巡检日志, 含 3 条 ERROR 行(连接被拒/权限不足/超时) + 1 条 WARN + 1 条 INFO, 以 multipart 上传;
        5. 预置一条已启用关键字规则(pattern="Connection refused"), 用于验证"已覆盖行不再重复建议"(覆盖去重);
        6. 巡检执行复用真实 worker 管线 run_inspection; 规则推荐执行复用真实管线 run_rule_suggest
           (测试进程内以真实 Redis 事件总线驱动), 执行前先 ensure_partitions 保证当月指标分区存在;
        7. 本用例不配置 LLM, 因此规则推荐走启发式降级路径(model=heuristic)。

    :TestStep
        1. owner 经 POST /api/tasks/upload(task_type=inspection) 上传日志, 运行真实巡检管线直至完成;
        2. 运行真实规则推荐管线 run_rule_suggest(task_id, run_id, ...);
        3. owner 经 GET /api/tasks/{task_id}/rule-suggestions 查询已持久化的推荐结果;
        4. owner 经 GET /api/tasks/{task_id}/rule-suggestions/events?run_id=&token= 订阅 SSE 事件流;
        5. 无关用户 other 经 POST/GET 访问不属于自己的任务;
        6. 对未完成(未跑管线)的巡检任务和新任务 id 触发推荐;
        7. 查询未生成推荐时的持久化结果。

    :ExpectResult
        1. 上传返回 200, 状态 pending; 管线跑完后任务状态 done;
        2. 推荐管线正常结束(不抛异常);
        3. 返回 200: assessment 非空且含"规则完善度评估", model=heuristic;
           items 含 pattern="permission denied"(未覆盖), 且不含 pattern="connection refused"(已被预置规则覆盖, 不重复建议);
        4. SSE 事件流依次包含 suggestion_token / suggestion / done 事件, 且 suggestion 事件携带未覆盖建议项;
        5. 触发返回 403"无权操作该任务", 查询返回 403"无权访问该任务";
        6. 未完成任务触发返回 409"任务尚未完成, 无法生成规则推荐", 不存在任务返回 404"任务不存在";
        7. 未生成时查询返回 404"尚未生成规则推荐"。

    :AfterTest
        1. 删除测试任务及其报告/指标/审计记录与上传文件目录; 删除临时用户 owner/other 及其审计;
        2. 删除预置的覆盖规则; flushdb Redis 测试库(db9), 不残留测试数据。
"""
import shutil
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.deps import get_redis
from app.core.security import create_access_token, hash_password
from app.events.event_bus import EventBus
from app.main import app
from app.metrics.store import ensure_partitions
from app.models.audit import AuditLog
from app.models.metric import MetricPoint
from app.models.rule import MatchType, Rule, Severity
from app.models.rule_suggestion import RuleSuggestion
from app.models.task import Report, Task
from app.models.user import User
from app.modules.inspection.pipeline import run_inspection
from app.modules.inspection.rule_suggest import run_rule_suggest

settings = get_settings()

OWNER = "__pytest_suggest_owner__"
OTHER = "__pytest_suggest_other__"
PASSWORD = "demo123"
COVER_RULE_NAME = "__pytest_suggest_cover_rule__"

INSPECTION_LOG = "\n".join([
    "2026-10-12 10:00:00 ERROR Connection refused to upstream service",
    "2026-10-12 10:00:01 ERROR Permission denied writing /var/log/app.log",
    "2026-10-12 10:00:02 ERROR request timeout while connecting to db",
    "2026-10-12 10:00:03 WARN memory usage high on node-1",
    "2026-10-12 10:00:04 INFO inspection heartbeat ok",
]) + "\n"


# ---------- 真实 infra 操作 (经 client.portal 在 app 的同一事件循环内执行) ----------
async def _seed_users():
    async with AsyncSessionLocal() as db:
        await _purge_users(db)
        owner = User(username=OWNER, email=f"{OWNER}@test.local", hashed_password=hash_password(PASSWORD),
                     display_name="推荐所有者", is_active=True, title="", dept="", avatar_hue=212)
        other = User(username=OTHER, email=f"{OTHER}@test.local", hashed_password=hash_password(PASSWORD),
                     display_name="无关用户", is_active=True, title="", dept="", avatar_hue=20)
        db.add_all([owner, other])
        await db.commit()
        return {"owner": owner.id, "other": other.id}


async def _purge_users(db):
    usernames = [OWNER, OTHER]
    ids = (await db.execute(select(User.id).where(User.username.in_(usernames)))).scalars().all()
    if ids:
        await db.execute(delete(AuditLog).where(AuditLog.user_id.in_(ids)))
    await db.execute(delete(AuditLog).where(AuditLog.user_name.in_(usernames)))
    await db.execute(delete(User).where(User.username.in_(usernames)))


async def _purge_users_commit():
    async with AsyncSessionLocal() as db:
        await _purge_users(db)
        await db.commit()


async def _seed_cover_rule():
    """预置一条已覆盖"Connection refused"的规则, 用于覆盖去重校验."""
    async with AsyncSessionLocal() as db:
        await _purge_cover_rule(db)
        db.add(Rule(name=COVER_RULE_NAME, description="测试用: 已覆盖连接拒绝",
                    match_type=MatchType.keyword, pattern="Connection refused",
                    severity=Severity.error, enabled=True, priority=1))
        await db.commit()


async def _purge_cover_rule(db):
    await db.execute(delete(Rule).where(Rule.name == COVER_RULE_NAME))


async def _purge_cover_rule_commit():
    async with AsyncSessionLocal() as db:
        await _purge_cover_rule(db)
        await db.commit()


async def _ensure_partitions():
    async with AsyncSessionLocal() as db:
        await ensure_partitions(db)


async def _run_inspection_pipeline(task_id: str):
    bus = EventBus(get_redis())

    async def is_cancelled() -> bool:
        return False

    await run_inspection(uuid.UUID(task_id), bus, is_cancelled)


async def _run_suggest_pipeline(task_id: str, run_id: str):
    bus = EventBus(get_redis())

    async def is_cancelled() -> bool:
        return False

    await run_rule_suggest(uuid.UUID(task_id), run_id, bus, is_cancelled)


async def _purge_task(task_id: str):
    tid = uuid.UUID(task_id)
    async with AsyncSessionLocal() as db:
        await db.execute(delete(MetricPoint).where(MetricPoint.task_id == task_id))
        await db.execute(delete(Report).where(Report.task_id == tid))
        await db.execute(delete(RuleSuggestion).where(RuleSuggestion.task_id == tid))
        await db.execute(delete(Task).where(Task.id == tid))
        await db.commit()


async def _flush_redis():
    await get_redis().flushdb()


@pytest.fixture(scope="module")
def client():
    """启动真实 app(lifespan) 的 TestClient; 所有请求与辅助操作复用同一事件循环."""
    with TestClient(app) as c:
        c.portal.call(_flush_redis)
        c.portal.call(_seed_cover_rule)
        yield c
        c.portal.call(_flush_redis)
        c.portal.call(_purge_cover_rule_commit)


@pytest.fixture(scope="module", autouse=True)
def seeded_users(client):
    ids = client.portal.call(_seed_users)
    yield ids
    client.portal.call(_purge_users_commit)


def _upload_inspection(client, token) -> str:
    resp = client.post(
        "/api/tasks/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("inspection.log", INSPECTION_LOG.encode("utf-8"), "text/plain")},
        data={"task_type": "inspection", "server_type": "generic"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["task_id"]


@pytest.fixture(scope="module")
def suggested_inspection(client, seeded_users):
    """:Preparation — owner 上传巡检并跑完巡检与规则推荐两条真实管线; 返回 task_id/令牌/run_id."""
    token = create_access_token(str(seeded_users["owner"]))
    task_id = _upload_inspection(client, token)

    client.portal.call(_ensure_partitions)          # 保证当月指标分区存在
    client.portal.call(_run_inspection_pipeline, task_id)  # 真实巡检管线执行直至完成

    # 触发规则推荐: 经 API 取 run_id, 再以真实管线执行(等价 worker 消费队列)
    resp = client.post(f"/api/tasks/{task_id}/rule-suggestions",
                       headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]
    client.portal.call(_run_suggest_pipeline, task_id, run_id)

    yield {"task_id": task_id, "token": token, "run_id": run_id}

    client.portal.call(_purge_task, task_id)
    shutil.rmtree(Path(settings.DATA_DIR) / "uploads" / task_id, ignore_errors=True)


# ---------- 1~3. 触发 → 执行 → 查询持久化结果 ----------
def test_rule_suggestion_persisted_result(client, suggested_inspection):
    task_id = suggested_inspection["task_id"]
    token = suggested_inspection["token"]

    # 任务已完成
    detail = client.get(f"/api/tasks/{task_id}", headers={"Authorization": f"Bearer {token}"})
    assert detail.status_code == 200
    assert detail.json()["status"] == "done"

    resp = client.get(f"/api/tasks/{task_id}/rule-suggestions",
                      headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["task_id"] == task_id
    assert body["model"] == "heuristic"                # 未配置 LLM → 启发式降级
    assert "规则完善度评估" in body["assessment"]

    patterns = {item["pattern"] for item in body["items"]}
    assert "permission denied" in patterns             # 未覆盖行 → 建议新增
    assert "connection refused" not in patterns        # 已被预置规则覆盖 → 不重复建议


# ---------- 4. SSE 事件流 ----------
def test_rule_suggestion_events_stream(client, suggested_inspection):
    task_id = suggested_inspection["task_id"]
    token = suggested_inspection["token"]
    run_id = suggested_inspection["run_id"]

    with client.stream(
        "GET", f"/api/tasks/{task_id}/rule-suggestions/events",
        params={"run_id": run_id, "token": token},
    ) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())

    assert "suggestion_token" in body
    assert "suggestion" in body
    assert "done" in body
    assert "permission denied" in body


# ---------- 5~7. 权限与前置校验 ----------
def test_rule_suggestion_access_control(client, suggested_inspection, seeded_users):
    task_id = suggested_inspection["task_id"]
    other_token = create_access_token(str(seeded_users["other"]))

    # 5. 无关用户触发 → 403(无权操作)
    denied_post = client.post(f"/api/tasks/{task_id}/rule-suggestions",
                              headers={"Authorization": f"Bearer {other_token}"})
    assert denied_post.status_code == 403
    assert denied_post.json()["detail"] == "无权操作该任务"

    # 5. 无关用户查询 → 403(无权访问)
    denied_get = client.get(f"/api/tasks/{task_id}/rule-suggestions",
                            headers={"Authorization": f"Bearer {other_token}"})
    assert denied_get.status_code == 403
    assert denied_get.json()["detail"] == "无权访问该任务"

    # 6. 不存在的任务 → 404
    missing = client.post(f"/api/tasks/{uuid.uuid4()}/rule-suggestions",
                          headers={"Authorization": f"Bearer {suggested_inspection['token']}"})
    assert missing.status_code == 404
    assert missing.json()["detail"] == "任务不存在"


# ---------- 6~7. 未完成任务 / 未生成推荐 ----------
def test_rule_suggestion_guards(client, seeded_users, suggested_inspection):
    token = create_access_token(str(seeded_users["owner"]))
    # 6. 上传后不跑管线(状态仍为 pending) → 触发返回 409
    pending_id = _upload_inspection(client, token)
    try:
        resp = client.post(f"/api/tasks/{pending_id}/rule-suggestions",
                           headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 409
        assert resp.json()["detail"] == "任务尚未完成, 无法生成规则推荐"

        # 7. 未生成推荐的(已存在但无推荐)任务查询 → 404
        none_resp = client.get(f"/api/tasks/{pending_id}/rule-suggestions",
                               headers={"Authorization": f"Bearer {token}"})
        assert none_resp.status_code == 404
        assert none_resp.json()["detail"] == "尚未生成规则推荐"
    finally:
        client.portal.call(_purge_task, pending_id)
        shutil.rmtree(Path(settings.DATA_DIR) / "uploads" / pending_id, ignore_errors=True)