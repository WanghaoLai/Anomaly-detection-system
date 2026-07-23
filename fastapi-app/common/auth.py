"""JWT、会话认证、Cookie 与密码哈希。"""
from datetime import datetime, timedelta, timezone
import hmac
import secrets
import uuid

import bcrypt
from fastapi import Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from models import Admin, AuthSession, User
from settings import (
    ACCESS_COOKIE_NAME,
    CSRF_COOKIE_NAME,
    JWT_ACCESS_EXPIRE_MINUTES,
    JWT_ALGORITHM,
    JWT_COOKIE_SAMESITE,
    JWT_COOKIE_SECURE,
    JWT_REFRESH_EXPIRE_DAYS,
    JWT_SECRET_KEY,
    REFRESH_COOKIE_NAME,
)


oauth2_scheme = HTTPBearer(auto_error=False)
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def is_bcrypt_hash(value: str | None) -> bool:
    return isinstance(value, str) and value.startswith(("$2a$", "$2b$", "$2y$"))


def hash_password(plaintext: str) -> str:
    data = plaintext.encode("utf-8")[:72]
    return bcrypt.hashpw(data, bcrypt.gensalt()).decode()


def verify_password(plaintext: str, stored: str) -> tuple:
    """校验密码，返回 (is_valid, needs_upgrade)。"""
    if plaintext is None or stored is None:
        return False, False

    data = plaintext.encode("utf-8")[:72]
    try:
        if bcrypt.checkpw(data, stored.encode()):
            return True, False
    except (ValueError, TypeError):
        pass

    if is_bcrypt_hash(stored):
        return False, False

    # 仅用于自动迁移遗留明文密码；数据迁移完成后应移除。
    if plaintext == stored:
        return True, True
    return False, False


def account_model_for_role(role: str):
    if role == "管理员":
        return Admin
    if role == "用户":
        return User
    return None


def _token_payload(
    account,
    role: str,
    session_id: str,
    token_type: str,
    expires_delta: timedelta,
    jti: str | None = None,
) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "sub": str(account.id),
        "username": account.username,
        "role": role,
        "ver": account.token_version,
        "sid": session_id,
        "type": token_type,
        "jti": jti or uuid.uuid4().hex,
        "iat": now,
        "exp": now + expires_delta,
    }


def create_access_token(account, role: str, session_id: str) -> str:
    payload = _token_payload(
        account,
        role,
        session_id,
        "access",
        timedelta(minutes=JWT_ACCESS_EXPIRE_MINUTES),
    )
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_refresh_token(
    account,
    role: str,
    session_id: str,
    refresh_jti: str,
) -> str:
    payload = _token_payload(
        account,
        role,
        session_id,
        "refresh",
        timedelta(days=JWT_REFRESH_EXPIRE_DAYS),
        jti=refresh_jti,
    )
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_token_pair(
    account,
    role: str,
    session_id: str,
    refresh_jti: str | None = None,
) -> tuple[str, str, str]:
    new_refresh_jti = refresh_jti or secrets.token_urlsafe(32)
    return (
        create_access_token(account, role, session_id),
        create_refresh_token(account, role, session_id, new_refresh_jti),
        new_refresh_jti,
    )


def decode_token(token: str, expected_type: str) -> dict | None:
    try:
        payload = jwt.decode(
            token,
            JWT_SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
            options={
                "require_exp": True,
                "require_iat": True,
                "require_sub": True,
            },
        )
        if payload.get("type") != expected_type:
            return None
        if payload.get("role") not in {"管理员", "用户"}:
            return None
        if not payload.get("username") or not payload.get("sid") or not payload.get("jti"):
            return None
        payload["user_id"] = int(payload["sub"])
        payload["ver"] = int(payload["ver"])
        return payload
    except (JWTError, KeyError, TypeError, ValueError):
        return None


def verify_access_token(token: str) -> dict | None:
    """仅验证 Access Token 的密码学完整性；请求认证还会查询数据库。"""
    return decode_token(token, "access")


async def authenticate_token(token: str, expected_type: str) -> tuple[dict, object, AuthSession]:
    payload = decode_token(token, expected_type)
    if payload is None:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")

    model = account_model_for_role(payload["role"])
    account = await model.get_or_none(id=payload["user_id"])
    if (
        account is None
        or account.role != payload["role"]
        or account.token_version != payload["ver"]
    ):
        raise HTTPException(status_code=401, detail="账号状态已变更，请重新登录")

    session = await AuthSession.get_or_none(
        id=payload["sid"],
        user_id=account.id,
        role=payload["role"],
    )
    now = datetime.now(timezone.utc)
    if (
        session is None
        or session.revoked_at is not None
        or session.expires_at <= now
        or (expected_type == "refresh" and session.refresh_jti != payload["jti"])
    ):
        raise HTTPException(status_code=401, detail="登录会话已失效，请重新登录")

    current_user = {
        "user_id": account.id,
        "username": account.username,
        "name": account.name,
        "avatar": account.avatar,
        "role": payload["role"],
        "token_version": account.token_version,
        "session_id": session.id,
    }
    return current_user, account, session


def validate_csrf(request: Request) -> None:
    csrf_cookie = request.cookies.get(CSRF_COOKIE_NAME)
    csrf_header = request.headers.get("X-CSRF-Token")
    if (
        not csrf_cookie
        or not csrf_header
        or not hmac.compare_digest(csrf_cookie, csrf_header)
    ):
        raise HTTPException(status_code=403, detail="CSRF 校验失败")


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def set_auth_cookies(
    response: Response,
    access_token: str,
    refresh_token: str,
    csrf_token: str,
) -> None:
    common = {
        "secure": JWT_COOKIE_SECURE,
        "samesite": JWT_COOKIE_SAMESITE,
    }
    response.set_cookie(
        ACCESS_COOKIE_NAME,
        access_token,
        httponly=True,
        max_age=JWT_ACCESS_EXPIRE_MINUTES * 60,
        path="/",
        **common,
    )
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        refresh_token,
        httponly=True,
        max_age=JWT_REFRESH_EXPIRE_DAYS * 24 * 60 * 60,
        path="/",
        **common,
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        csrf_token,
        httponly=False,
        max_age=JWT_REFRESH_EXPIRE_DAYS * 24 * 60 * 60,
        path="/",
        **common,
    )


def clear_auth_cookies(response: Response) -> None:
    for name in (ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME, CSRF_COOKIE_NAME):
        response.delete_cookie(
            name,
            path="/",
            secure=JWT_COOKIE_SECURE,
            samesite=JWT_COOKIE_SAMESITE,
        )


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(oauth2_scheme),
) -> dict:
    if credentials is not None:
        token = credentials.credentials
    else:
        token = request.cookies.get(ACCESS_COOKIE_NAME)
        if token and request.method.upper() in UNSAFE_METHODS:
            validate_csrf(request)

    if not token:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    current_user, _, _ = await authenticate_token(token, "access")
    return current_user


async def get_current_admin(
    current_user: dict = Depends(get_current_user),
) -> dict:
    if current_user["role"] != "管理员":
        raise HTTPException(status_code=403, detail="无管理员权限")
    return current_user
