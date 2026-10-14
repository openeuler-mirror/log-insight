"""性能分析: sar 文件上传解析接口 (POST /api/perf/sar/analyze) 测试

    :Preparation
        1. 依赖本地真实基础设施: PostgreSQL + Redis(测试专用 db9), 连接由 backend/conftest.py 在导入 app 前注入;
        2. 以 `with TestClient(app)` 启动真实 lifespan(Redis/schema 守卫/建表/幂等种子);
        3. 通过 SQLAlchemy 真实写入一个临时用户, 令牌由 create_access_token 直接签发, 免去登录流程;
        4. 准备一份含 CPU / 内存 / 磁盘 三段的 sar 文本样本。

    :TestStep
        1. 携带令牌 multipart 上传样本;
        2. 不携带令牌上传同一文件;
        3. 携带令牌上传空文件;
        4. 携带令牌上传无法识别为 sar 的文本。

    :ExpectResult
        1. 200, format=sar, views=[cpu, mem, disk], 各段含 points 列表与聚合值;
        2. 401 (鉴权拦截, 不进入解析);
        3. 400 且 detail 含"文件为空";
        4. 400 且 detail 含"未识别"。

    :AfterTest
        1. 删除临时用户及其审计记录(先删审计规避外键);
        2. flushdb Redis 测试库(db9), 不残留测试数据。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal
from app.core.deps import get_redis
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.audit import AuditLog
from app.models.user import User

USER = "__pytest_perf_sar__"
PASSWORD = "demo123"

SAMPLE_SAR = """Linux 5.10.0-60.18.0.50.oe2203.aarch64 (node-1) \t10/14/2026 \t_aarch64_\t(4 CPU)

12:00:01 AM     CPU     %user     %nice   %system   %iowait    %steal     %idle
12:00:11 AM     all      1.50      0.00      0.75      0.25      0.00     97.50
12:00:21 AM     all      3.00      0.00      1.00      2.00      0.00     94.00
Average:        all      2.25      0.00      0.88      1.13      0.00     95.75

12:00:01 AM kbmemfree kbavail kbmemused  %memused kbbuffers  kbcached
12:00:11 AM   1000000 1200000   3000000     75.00    100000    500000
12:00:21 AM    900000 1100000   3100000     77.50    100000    500000

12:00:01 AM       DEV       tps  rd_sec/s  wr_sec/s  avgrq-sz  avgqu-sz     await     svctm     %util
12:00:11 AM    dev8-0      1.00    100.00    200.00     10.00      0.10      5.00      1.00     10.00
12:00:21 AM    dev8-0      3.00    200.00    400.00     30.00      0.30     60.00      3.00     85.00
"""


# ---------- 真实 infra 操作 (经 client.portal 在 app 的同一事件循环内执行) ----------
async def _purge(db):
    ids = (await db.execute(select(User.id).where(User.username == USER))).scalars().all()
    if ids:
        await db.execute(delete(AuditLog).where(AuditLog.user_id.in_(ids)))
    await db.execute(delete(AuditLog).where(AuditLog.user_name == USER))
    await db.execute(delete(User).where(User.username == USER))
    await db.commit()


async def _purge_all():
    async with AsyncSessionLocal() as db:
        await _purge(db)


async def _seed():
    async with AsyncSessionLocal() as db:
        await _purge(db)
        user = User(username=USER, email=f"{USER}@test.local", hashed_password=hash_password(PASSWORD),
                    display_name="性能分析测试用户", is_active=True, title="", dept="", avatar_hue=212)
        db.add(user)
        await db.commit()
        return {"user": user.id}


async def _flush_redis():
    await get_redis().flushdb()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        c.portal.call(_flush_redis)
        yield c
        c.portal.call(_flush_redis)


@pytest.fixture(scope="module")
def token(client):
    ids = client.portal.call(_seed)
    yield create_access_token(str(ids["user"]))
    client.portal.call(_purge_all)


def _upload(client, token, content: bytes, filename: str = "sar.txt"):
    return client.post(
        "/api/perf/sar/analyze",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": (filename, content, "text/plain")},
    )


def test_sar_analyze_ok(client, token):
    resp = _upload(client, token, SAMPLE_SAR.encode("utf-8"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["format"] == "sar"
    assert body["views"] == ["cpu", "mem", "disk"]
    assert body["cpu"]["points_total"] == 2          # Average 行跳过
    assert body["cpu"]["avg"]["busy"] == 4.25
    assert body["mem"]["avg"]["mem_used_pct"] == 76.25
    dev0 = next(d for d in body["disk"]["devices"] if d["dev"] == "dev8-0")
    assert dev0["avg_read_kbps"] == 75.0             # rd_sec/s ÷2
    assert dev0["max_util_pct"] == 85.0


def test_sar_analyze_requires_auth(client):
    resp = client.post("/api/perf/sar/analyze",
                       files={"file": ("sar.txt", SAMPLE_SAR.encode("utf-8"), "text/plain")})
    assert resp.status_code == 401


def test_sar_analyze_empty_file(client, token):
    resp = _upload(client, token, b"")
    assert resp.status_code == 400
    assert "文件为空" in resp.json()["detail"]


def test_sar_analyze_unrecognized_text(client, token):
    resp = _upload(client, token, b"plain text without any sar section\n")
    assert resp.status_code == 400
    assert "未识别" in resp.json()["detail"]