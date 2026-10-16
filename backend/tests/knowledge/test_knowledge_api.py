"""个人知识库: 笔记 CRUD 与个人隔离 (GET/POST/PUT/DELETE /api/knowledge) 测试

    :Preparation
        1. 依赖本地真实基础设施: PostgreSQL + Redis(测试专用 db9), 连接由 backend/conftest.py 在导入 app 前注入;
        2. 以 `with TestClient(app)` 启动真实 lifespan(Redis/schema 守卫/建表/幂等种子);
        3. 通过 SQLAlchemy 真实写入两个临时用户 A(alice)/B(bob), 令牌由 create_access_token 直接签发;
        4. 为 A 预置一条笔记(tags=["kafka","积压"]), 为 B 预置一条笔记(tags=["bonly"])。

    :TestStep
        1. 未携带令牌访问列表;
        2. A 列表;
        3. A 按关键字搜索(命中 / 不命中);
        4. A 按标签筛选(命中 / 不命中);
        5. A 新建笔记;
        6. A 更新自己的笔记;
        7. A 新建后删除, 再查详情;
        8. B 读/改/删 A 的笔记。

    :ExpectResult
        1. 401 (鉴权拦截);
        2. 仅含 A 的笔记, 不含 B 的笔记(个人私有);
        3. 命中含 A 的笔记(标题/正文 ILIKE); 不命中返回空列表;
        4. tag=kafka 命中 A 的笔记; tag=bonly 对 A 返回空列表;
        5. 201, 响应含 id/owner_id/title/tags/source_type/created_at/updated_at, owner_id 为 A;
        6. 200 且 title 已更新;
        7. 删除返回 200; 再查详情 404;
        8. 均返回 404 (非本人资源一律不可见/不可操作)。

    :AfterTest
        1. 删除两个临时用户的知识笔记与用户本身(先删笔记, 规避外键);
        2. 清空 Redis 测试库(db9), 不残留测试数据。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal
from app.core.deps import get_redis
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.knowledge import KnowledgeNote
from app.models.user import User

USER_A = "__pytest_kb_alice__"
USER_B = "__pytest_kb_bob__"
PASSWORD = "demo123"


# ---------- 真实 infra 操作 (经 client.portal 在 app 的同一事件循环内执行) ----------
async def _purge(db):
    ids = (await db.execute(
        select(User.id).where(User.username.in_([USER_A, USER_B])))).scalars().all()
    if ids:
        await db.execute(delete(KnowledgeNote).where(KnowledgeNote.owner_id.in_(ids)))
    await db.execute(delete(User).where(User.username.in_([USER_A, USER_B])))
    await db.commit()


async def _purge_all():
    async with AsyncSessionLocal() as db:
        await _purge(db)


async def _seed():
    async with AsyncSessionLocal() as db:
        await _purge(db)
        a = User(username=USER_A, email=f"{USER_A}@test.local", hashed_password=hash_password(PASSWORD),
                 display_name="知识库测试A", is_active=True, title="", dept="", avatar_hue=212)
        b = User(username=USER_B, email=f"{USER_B}@test.local", hashed_password=hash_password(PASSWORD),
                 display_name="知识库测试B", is_active=True, title="", dept="", avatar_hue=212)
        db.add_all([a, b])
        await db.flush()
        note_a = KnowledgeNote(owner_id=a.id, title="Kafka 消费堆积排查",
                               content="检查 consumer lag 与 broker 负载", tags=["kafka", "积压"],
                               source_type="manual")
        note_b = KnowledgeNote(owner_id=b.id, title="只属于B的笔记",
                               content="B 的私有内容", tags=["bonly"], source_type="manual")
        db.add_all([note_a, note_b])
        await db.commit()
        return {"a": a.id, "b": b.id, "note_a": note_a.id, "note_b": note_b.id}


async def _flush_redis():
    await get_redis().flushdb()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        c.portal.call(_flush_redis)
        yield c
        c.portal.call(_flush_redis)


@pytest.fixture(scope="module")
def ctx(client):
    data = client.portal.call(_seed)
    yield {**data, "ta": create_access_token(str(data["a"])),
           "tb": create_access_token(str(data["b"]))}
    client.portal.call(_purge_all)


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def test_list_requires_auth(client):
    assert client.get("/api/knowledge").status_code == 401


def test_list_returns_owned_only(client, ctx):
    rows = client.get("/api/knowledge", headers=_h(ctx["ta"])).json()
    ids = {r["id"] for r in rows}
    assert ctx["note_a"] in ids
    assert ctx["note_b"] not in ids          # 个人私有: 看不到他人笔记


def test_search_by_keyword_hit_and_miss(client, ctx):
    hit = client.get("/api/knowledge", params={"q": "Kafka"}, headers=_h(ctx["ta"])).json()
    assert any(r["id"] == ctx["note_a"] for r in hit)
    miss = client.get("/api/knowledge", params={"q": "绝对不存在的关键词xyz"},
                      headers=_h(ctx["ta"])).json()
    assert miss == []


def test_filter_by_tag(client, ctx):
    hit = client.get("/api/knowledge", params={"tag": "kafka"}, headers=_h(ctx["ta"])).json()
    assert any(r["id"] == ctx["note_a"] for r in hit)
    empty = client.get("/api/knowledge", params={"tag": "bonly"}, headers=_h(ctx["ta"])).json()
    assert empty == []                       # B 的标签对 A 不可见


def test_create_note(client, ctx):
    resp = client.post("/api/knowledge", headers=_h(ctx["ta"]),
                       json={"title": "新建笔记", "content": "正文内容",
                             "tags": ["新", "测试"], "source_type": "manual"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["owner_id"] == ctx["a"]
    assert body["title"] == "新建笔记"
    assert body["tags"] == ["新", "测试"]
    assert body["source_type"] == "manual"
    assert body["created_at"] and body["updated_at"]
    # 清理, 避免污染其它用例
    assert client.delete(f"/api/knowledge/{body['id']}", headers=_h(ctx["ta"])).status_code == 200


def test_update_note(client, ctx):
    resp = client.put(f"/api/knowledge/{ctx['note_a']}", headers=_h(ctx["ta"]),
                      json={"title": "Kafka 消费堆积排查(已更新)", "content": "更新后的正文",
                            "tags": ["kafka"], "source_type": "manual"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "Kafka 消费堆积排查(已更新)"
    assert body["content"] == "更新后的正文"


def test_delete_then_get_404(client, ctx):
    created = client.post("/api/knowledge", headers=_h(ctx["ta"]),
                          json={"title": "待删除", "content": "", "tags": []}).json()
    assert client.delete(f"/api/knowledge/{created['id']}", headers=_h(ctx["ta"])).status_code == 200
    assert client.get(f"/api/knowledge/{created['id']}", headers=_h(ctx["ta"])).status_code == 404


def test_other_user_cannot_read_update_delete(client, ctx):
    note_id = ctx["note_a"]
    assert client.get(f"/api/knowledge/{note_id}", headers=_h(ctx["tb"])).status_code == 404
    assert client.put(f"/api/knowledge/{note_id}", headers=_h(ctx["tb"]),
                      json={"title": "hack", "content": "", "tags": []}).status_code == 404
    assert client.delete(f"/api/knowledge/{note_id}", headers=_h(ctx["tb"])).status_code == 404
