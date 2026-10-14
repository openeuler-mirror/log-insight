"""认证模块(登录 / 令牌 / 登出 / 当前用户)功能测试

    :Preparation
        1. 依赖本地真实基础设施: PostgreSQL + Redis; 连接信息由 backend/conftest.py 在导入 app 前注入
           (DATABASE_URL=postgresql+asyncpg://loginsight:loginsight@127.0.0.1:5432/loginsight,
            REDIS_URL=redis://127.0.0.1:6379/9 测试专用 db9);
        2. 以 `with TestClient(app)` 启动真实 lifespan: 建 Redis 客户端 + schema_guard + create_all +
           权限/角色/管理员幂等种子(SEED_DEMO_DATA=False 跳过演示数据); 全部请求复用同一事件循环;
        3. 用例通过 SQLAlchemy 真实写入两个临时用户: 启用 alice(密码 demo123)与禁用 bob;
           DB/Redis 辅助操作经 client.portal 在同一事件循环内执行, 不使用任何内存假实现;
        4. 令牌/密码纯函数(签发、校验、哈希)直接调用 app.core.security, 无需 HTTP。

    :TestStep
        1. 密码: hash_password 生成非明文密文; verify_password 校验正确密码通过、错误密码失败;
        2. 令牌签发: create_access_token / create_refresh_token 声明的类型(type)与解码回的 user id;
        3. 令牌校验: 类型不符(access 当 refresh 用)、被篡改、已过期 三种情况 decode_token 应判定无效;
        4. 登录 /api/auth/login: 真实用户正确密码 / 用户名不存在 / 密码错误 / 禁用用户;
        5. 刷新 /api/auth/refresh: 合法 refresh 令牌换发新令牌 / 用 access 令牌冒充 / 伪造令牌;
        6. 登出 /api/auth/logout: 携带令牌登出, 校验真实 Redis 写入黑名单键, 再用该令牌访问受保护接口;
        7. 当前用户 /api/auth/me: 未携带令牌 / 携带合法令牌。

    :ExpectResult
        1. 密文不等于明文, 正确密码校验为 True, 错误密码为 False;
        2. access 与 refresh 均能解析出同一 user id, 且两种类型不可互换(解析均为 None);
        3. 篡改 / 过期 / 类型不符的令牌解析结果均为 None;
        4. 登录成功返回 200 且含 access_token/refresh_token/token_type=bearer, 并在 audit_logs 落库一条成功记录;
           用户名不存在与密码错误均返回 401"用户名或密码错误"且落库失败审计(user_id 为空); 禁用用户返回 403"用户已禁用";
        5. 合法刷新返回 200 的新双令牌; 用 access 冒充与伪造令牌刷新均返回 401;
        6. 登出返回 200, 真实 Redis(db9) 中存在键 bl:<token>; 再用该令牌访问 /me 返回 401"令牌已注销";
        7. 未认证访问 /me 返回 401"未认证"; 合法令牌返回 200, username 为 alice 且 team_roles 为空。

    :AfterTest
        1. 删除临时用户(alice/bob)及其产生的审计记录(先删审计再删用户, 规避外键约束);
        2. 清空 Redis 测试库(db9), 还原测试前状态; 不残留任何测试数据。
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import delete, or_, select

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.deps import get_redis
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.main import app
from app.models.audit import AuditLog
from app.models.user import User

settings = get_settings()

ALICE = "__pytest_auth_alice__"
BOB = "__pytest_auth_bob__"
GHOST = "__pytest_auth_ghost__"
PASSWORD = "demo123"


# ---------- 真实 infra 操作 (经 client.portal 在 app 的同一事件循环内执行) ----------
async def _purge(db):
    """删除临时用户及其审计记录(先删审计, 规避 audit_logs.user_id 外键)."""
    usernames = [ALICE, BOB, GHOST]
    ids = (await db.execute(select(User.id).where(User.username.in_(usernames)))).scalars().all()
    if ids:
        await db.execute(delete(AuditLog).where(AuditLog.user_id.in_(ids)))
    await db.execute(delete(AuditLog).where(AuditLog.user_name.in_(usernames)))
    await db.execute(delete(User).where(User.username.in_(usernames)))
    await db.commit()


async def _purge_all():
    async with AsyncSessionLocal() as db:
        await _purge(db)


async def _seed():
    async with AsyncSessionLocal() as db:
        await _purge(db)
        alice = User(username=ALICE, email=f"{ALICE}@test.local", hashed_password=hash_password(PASSWORD),
                     display_name="测试爱丽丝", is_active=True, title="运维工程师", dept="测试部", avatar_hue=212)
        bob = User(username=BOB, email=f"{BOB}@test.local", hashed_password=hash_password(PASSWORD),
                   display_name="测试鲍勃", is_active=False, title="", dept="", avatar_hue=212)
        db.add_all([alice, bob])
        await db.commit()
        return {"alice": alice.id, "bob": bob.id}


async def _audit_rows(username, user_id=None):
    """按用户名(失败登录记录 user_name=尝试用户名)或用户 id(成功登录记录 user_name=展示名)取审计."""
    async with AsyncSessionLocal() as db:
        cond = AuditLog.user_name == username
        if user_id is not None:
            cond = or_(cond, AuditLog.user_id == user_id)
        result = await db.execute(
            select(AuditLog.action, AuditLog.success, AuditLog.user_id).where(cond).order_by(AuditLog.id))
        return [tuple(row) for row in result.all()]


async def _redis_get(key):
    return await get_redis().get(key)


async def _redis_del(key):
    await get_redis().delete(key)


async def _flush_redis():
    await get_redis().flushdb()


@pytest.fixture(scope="module")
def client():
    """启动真实 app(lifespan) 的 TestClient; 所有请求与辅助操作复用同一事件循环."""
    with TestClient(app) as c:
        c.portal.call(_flush_redis)  # 清理上个会话可能残留的黑名单键
        yield c
        c.portal.call(_flush_redis)


@pytest.fixture(scope="module", autouse=True)
def seeded_users(client):
    """:Preparation / :AfterTest —— 真实写入临时用户, 结束后清理."""
    ids = client.portal.call(_seed)
    yield ids
    client.portal.call(_purge_all)


# ---------- 1. 密码哈希 ----------
def test_password_hash_is_not_plaintext_and_verifies():
    hashed = hash_password("demo123")
    assert hashed != "demo123"
    assert verify_password("demo123", hashed) is True
    assert verify_password("wrong-password", hashed) is False


# ---------- 2~3. 令牌签发与校验 ----------
def test_access_token_roundtrip():
    assert decode_token(create_access_token("42"), "access") == "42"


def test_refresh_token_roundtrip():
    assert decode_token(create_refresh_token("42"), "refresh") == "42"


def test_token_types_not_interchangeable():
    assert decode_token(create_access_token("1"), "refresh") is None
    assert decode_token(create_refresh_token("1"), "access") is None


def test_tampered_or_garbage_token_is_invalid():
    token = create_access_token("1")
    assert decode_token(token + "x", "access") is None
    assert decode_token("not-a-jwt", "access") is None


def test_expired_token_is_invalid():
    expired = jwt.encode(
        {"sub": "1", "type": "access",
         "exp": datetime.now(timezone.utc) - timedelta(seconds=1)},
        settings.SECRET_KEY, algorithm=settings.ALGORITHM,
    )
    assert decode_token(expired, "access") is None


# ---------- 4. 登录接口 (真实 PG) ----------
def test_login_success_returns_tokens(client, seeded_users):
    resp = client.post("/api/auth/login", json={"username": ALICE, "password": PASSWORD})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert decode_token(body["access_token"], "access") == str(seeded_users["alice"])
    assert decode_token(body["refresh_token"], "refresh") == str(seeded_users["alice"])
    # 真实校验审计落库 (成功登录 user_name 取展示名, 故按 user_id 匹配)
    assert ("登录平台", True, seeded_users["alice"]) in client.portal.call(
        _audit_rows, ALICE, seeded_users["alice"])


def test_login_unknown_user_401(client):
    resp = client.post("/api/auth/login", json={"username": GHOST, "password": PASSWORD})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "用户名或密码错误"
    # 失败登录同样落库, user_id 为空
    assert ("登录平台", False, None) in client.portal.call(_audit_rows, GHOST)


def test_login_wrong_password_401(client, seeded_users):
    resp = client.post("/api/auth/login", json={"username": ALICE, "password": "bad-password"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "用户名或密码错误"
    assert ("登录平台", False, None) in client.portal.call(_audit_rows, ALICE)


def test_login_disabled_user_403(client, seeded_users):
    resp = client.post("/api/auth/login", json={"username": BOB, "password": PASSWORD})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "用户已禁用"


# ---------- 5. 刷新接口 (真实 PG) ----------
def test_refresh_success_issues_new_tokens(client, seeded_users):
    resp = client.post("/api/auth/refresh",
                       json={"refresh_token": create_refresh_token(str(seeded_users["alice"]))})
    assert resp.status_code == 200
    assert decode_token(resp.json()["access_token"], "access") == str(seeded_users["alice"])


def test_refresh_with_access_token_401(client, seeded_users):
    resp = client.post("/api/auth/refresh",
                       json={"refresh_token": create_access_token(str(seeded_users["alice"]))})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "刷新令牌无效或已过期"


def test_refresh_with_forged_token_401(client):
    resp = client.post("/api/auth/refresh", json={"refresh_token": "forged.token.value"})
    assert resp.status_code == 401


# ---------- 6. 登出接口 (真实 Redis) ----------
def test_logout_blacklists_token_and_blocks_reuse(client, seeded_users):
    token = create_access_token(str(seeded_users["alice"]))
    resp = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    # 真实 Redis 中存在黑名单键
    assert client.portal.call(_redis_get, f"bl:{token}") == "1"
    # 已注销令牌再访问受保护接口 → 被黑名单拦截
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 401
    assert me.json()["detail"] == "令牌已注销"
    # 清理黑名单键: 同一秒内为同一用户签发的 JWT 完全相同, 否则会误伤后续用例
    client.portal.call(_redis_del, f"bl:{token}")


# ---------- 7. 当前用户接口 (真实 PG) ----------
def test_me_requires_auth(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "未认证"


def test_me_returns_profile(client, seeded_users):
    token = create_access_token(str(seeded_users["alice"]))
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == ALICE
    assert body["is_active"] is True
    assert body["team_roles"] == []