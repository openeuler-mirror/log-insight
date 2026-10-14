"""智能巡检: 任务详情查看流程(上传巡检 → 进度完成 → 查看详情)测试

    :Preparation
        1. 依赖本地真实基础设施: PostgreSQL + Redis(测试专用 db9); 连接由 backend/conftest.py 在导入 app 前注入;
        2. 以 `with TestClient(app)` 启动真实 lifespan(Redis/schema 守卫/幂等种子); 全部请求与辅助操作复用同一事件循环;
        3. 准备两个真实用户: 任务所有者 owner 与 无关用户 other, 令牌由 create_access_token 直接签发;
        4. 准备一份可解析的巡检日志(含 ERROR 行, 供 error_log 检查项命中), 以 multipart 上传;
        5. 巡检执行复用真实 worker 管线 run_inspection(测试进程内以真实 Redis 事件总线驱动),
           执行前先 ensure_partitions 保证当月指标分区存在(否则指标写入失败);
        6. 详情展示页内容与结果准确性由另外的测试用例保证, 本用例只覆盖"可查看详情"的流程与归属校验。

    :TestStep
        1. owner 经 POST /api/tasks/upload(task_type=inspection) 上传日志发起巡检, 得到 task_id;
        2. 运行真实巡检管线 run_inspection 直至任务完成(等价 worker 消费队列);
        3. owner 经 GET /api/tasks?type=inspection 查询本人任务列表;
        4. owner 经 GET /api/tasks/{task_id} 点击"查看详情";
        5. 无关用户 other 经 GET /api/tasks/{task_id} 查看不属于自己的任务详情;
        6. owner 经 GET /api/tasks/{task_id} 查看一个不存在的任务 id。

    :ExpectResult
        1. 上传返回 200, 状态 pending, 类型 inspection;
        2. 完成后状态 done, 进度 100;
        3. 任务列表中包含该 task_id(巡检页可见, 可点击);
        4. 详情返回 200, id/type=inspection/status=done/progress=100/creator_name 与 owner 一致;
        5. 返回 403"无权访问该任务"(仅本人/管理员/同团队成员可见);
        6. 返回 404"任务不存在"。

    :AfterTest
        1. 删除测试任务及其报告/指标/审计记录与上传文件目录; 删除临时用户 owner/other 及其审计;
        2. flushdb Redis 测试库(db9), 不残留测试数据。
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
from app.models.task import Report, Task
from app.models.user import User
from app.modules.inspection.pipeline import run_inspection

settings = get_settings()

OWNER = "__pytest_inspection_owner__"
OTHER = "__pytest_inspection_other__"
PASSWORD = "demo123"

INSPECTION_LOG = "\n".join([
    "2026-10-12 10:00:00 ERROR disk sda read failure on node-1",
    "2026-10-12 10:00:01 ERROR cpu thermal throttle on node-1",
    "2026-10-12 10:00:02 WARN memory usage high on node-1",
    "2026-10-12 10:00:03 INFO inspection heartbeat ok",
]) + "\n"


# ---------- 真实 infra 操作 (经 client.portal 在 app 的同一事件循环内执行) ----------
async def _seed_users():
    async with AsyncSessionLocal() as db:
        await _purge_users(db)
        owner = User(username=OWNER, email=f"{OWNER}@test.local", hashed_password=hash_password(PASSWORD),
                     display_name="巡检所有者", is_active=True, title="", dept="", avatar_hue=212)
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


async def _ensure_partitions():
    async with AsyncSessionLocal() as db:
        await ensure_partitions(db)


async def _run_pipeline(task_id: str):
    """等价 worker: 以真实 Redis 事件总线驱动巡检管线直至完成."""
    bus = EventBus(get_redis())

    async def is_cancelled() -> bool:
        return False

    await run_inspection(uuid.UUID(task_id), bus, is_cancelled)


async def _purge_task(task_id: str):
    tid = uuid.UUID(task_id)
    async with AsyncSessionLocal() as db:
        await db.execute(delete(MetricPoint).where(MetricPoint.task_id == task_id))
        await db.execute(delete(Report).where(Report.task_id == tid))
        await db.execute(delete(Task).where(Task.id == tid))
        await db.commit()


async def _flush_redis():
    await get_redis().flushdb()


@pytest.fixture(scope="module")
def client():
    """启动真实 app(lifespan) 的 TestClient; 所有请求与辅助操作复用同一事件循环."""
    with TestClient(app) as c:
        c.portal.call(_flush_redis)
        yield c
        c.portal.call(_flush_redis)


@pytest.fixture(scope="module", autouse=True)
def seeded_users(client):
    ids = client.portal.call(_seed_users)
    yield ids
    client.portal.call(_purge_users_commit)


@pytest.fixture(scope="module")
def completed_inspection(client, seeded_users):
    """:Preparation — owner 上传巡检日志并跑完真实管线; 返回 (task_id, 上传响应, owner 令牌)."""
    token = create_access_token(str(seeded_users["owner"]))
    resp = client.post(
        "/api/tasks/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("inspection.log", INSPECTION_LOG.encode("utf-8"), "text/plain")},
        data={"task_type": "inspection", "server_type": "generic"},
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["task_id"]

    client.portal.call(_ensure_partitions)   # 保证当月指标分区存在
    client.portal.call(_run_pipeline, task_id)  # 真实巡检管线执行直至完成

    yield {"task_id": task_id, "upload": resp.json(), "token": token}

    client.portal.call(_purge_task, task_id)
    shutil.rmtree(Path(settings.DATA_DIR) / "uploads" / task_id, ignore_errors=True)


# ---------- 1~2. 上传发起巡检 + 进度完成 ----------
def test_upload_then_complete_inspection_task(client, completed_inspection):
    upload = completed_inspection["upload"]
    assert upload["status"] == "pending"  # 入队即返回
    detail = client.get(f"/api/tasks/{completed_inspection['task_id']}",
                        headers={"Authorization": f"Bearer {completed_inspection['token']}"})
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["type"] == "inspection"
    assert body["status"] == "done"       # 进度完成后
    assert body["progress"] == 100


# ---------- 3. 巡检页任务列表可见(可点击查看详情) ----------
def test_inspection_task_appears_in_owner_list(client, completed_inspection):
    resp = client.get("/api/tasks", params={"type": "inspection"},
                      headers={"Authorization": f"Bearer {completed_inspection['token']}"})
    assert resp.status_code == 200
    ids = [t["id"] for t in resp.json()]
    assert completed_inspection["task_id"] in ids


# ---------- 4~6. 查看详情与归属校验 ----------
def test_inspection_detail_view_and_access_control(client, completed_inspection, seeded_users):
    task_id = completed_inspection["task_id"]
    owner_token = completed_inspection["token"]
    # 4. owner 查看自己的任务详情 → 200
    resp = client.get(f"/api/tasks/{task_id}", headers={"Authorization": f"Bearer {owner_token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == task_id
    assert body["type"] == "inspection"
    assert body["status"] == "done"
    assert body["progress"] == 100
    assert body["creator_name"] == "巡检所有者"

    # 5. 无关用户查看他人任务详情 → 403
    other_token = create_access_token(str(seeded_users["other"]))
    other = client.get(f"/api/tasks/{task_id}", headers={"Authorization": f"Bearer {other_token}"})
    assert other.status_code == 403
    assert other.json()["detail"] == "无权访问该任务"

    # 6. 查看不存在的任务 → 404
    missing = client.get(f"/api/tasks/{uuid.uuid4()}", headers={"Authorization": f"Bearer {owner_token}"})
    assert missing.status_code == 404
    assert missing.json()["detail"] == "任务不存在"