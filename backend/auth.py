"""
认证模块 - 密码哈希、会话管理、FastAPI 鉴权依赖

设计说明：
- 密码用标准库 hashlib.pbkdf2_hmac（20 万轮 sha256），不引入 bcrypt/passlib 依赖，
  避免云端装依赖时踩编译坑。
- 会话用随机 token 存 sessions 表 + HttpOnly Cookie，不引入 JWT 库。
- 第一个注册的用户自动成为管理员。
"""
import hashlib
import hmac
import re
import secrets

from fastapi import HTTPException, Request

import database as db

PBKDF2_ITERATIONS = 200_000
COOKIE_NAME = "edu_session"
USERNAME_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fa5]{2,20}$")


# ============ Password ============

def hash_password(password: str) -> str:
    """生成 pbkdf2 密码哈希，格式: pbkdf2_sha256$轮数$盐$摘要"""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """恒定时间比对，防时序攻击"""
    try:
        algo, iters, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iters)
        )
        return hmac.compare_digest(dk.hex(), digest)
    except Exception:
        return False


def validate_credentials(username: str, password: str) -> str:
    """校验注册输入，返回错误信息（空字符串表示通过）"""
    if not username or not password:
        return "用户名和密码不能为空"
    if not USERNAME_RE.match(username):
        return "用户名需为 2-20 位中文、字母、数字或下划线"
    if len(password) < 6:
        return "密码至少 6 位"
    if len(password) > 128:
        return "密码过长"
    return ""


# ============ Session ============

def new_token() -> str:
    return secrets.token_urlsafe(32)


async def get_current_user(request: Request) -> dict:
    """
    FastAPI 依赖：解析当前登录用户。
    未登录 / 会话过期一律 401，前端据此跳转登录页。
    """
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="未登录")
    user = await db.get_session_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    return user


def set_session_cookie(response, token: str):
    """写入会话 Cookie。

    - httponly: JS 读不到，防 XSS 窃取
    - samesite=lax: 防 CSRF，同时保证正常导航携带
    - secure=False: 因为既要支持本地 http 调试，也要支持云端 https
    """
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=db.SESSION_DAYS * 24 * 3600,
        httponly=True,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response):
    response.delete_cookie(key=COOKIE_NAME, path="/")
