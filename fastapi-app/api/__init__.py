import importlib
import pkgutil
from datetime import datetime, timedelta, timezone
from typing import Literal
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict

from common.auth import (
    authenticate_token,
    clear_auth_cookies,
    create_token_pair,
    get_current_user,
    hash_password,
    new_csrf_token,
    set_auth_cookies,
    validate_csrf,
    verify_password,
)
from common.exception_handler import CustomException
from common.login_rate_limiter import login_rate_limiter
from common.result import Result
from models import Admin, AuthSession, User
from settings import (
    ACCESS_COOKIE_NAME,
    CSRF_COOKIE_NAME,
    JWT_REFRESH_EXPIRE_DAYS,
    REFRESH_COOKIE_NAME,
)


class Account(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = None
    username: str = None
    password: str = None
    newPassword: str = None
    role: str = None
    name: str = None
    avatar: str = None


class PasswordUpdateRequest(BaseModel):
    password: str
    newPassword: str


class LoginRequest(BaseModel):
    username: str
    password: str
    role: Literal["管理员", "用户"]


api_router = APIRouter()
DUMMY_PASSWORD_HASH = hash_password("__invalid_login_password__")


def _user_data(user, role: str) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "name": user.name,
        "avatar": user.avatar,
        "role": role,
    }


# 登录
@api_router.post("/login")
async def login(
    account: LoginRequest,
    request: Request,
    response: Response,
):
    client_ip = request.client.host if request.client else "unknown"
    await login_rate_limiter.check(client_ip, account.username, account.role)

    if account.role == '管理员':
        user = await Admin.get_or_none(username=account.username)
    else:
        user = await User.get_or_none(username=account.username)

    if user is None:
        # 对不存在的账号执行同等成本的 bcrypt，降低计时枚举风险。
        verify_password(account.password, DUMMY_PASSWORD_HASH)
        await login_rate_limiter.record_failure(
            client_ip,
            account.username,
            account.role,
        )
        raise CustomException("账号或密码错误")

    is_valid, needs_upgrade = verify_password(account.password, user.password)
    if not is_valid or user.role != account.role:
        await login_rate_limiter.record_failure(
            client_ip,
            account.username,
            account.role,
        )
        raise CustomException("账号或密码错误")

    if needs_upgrade:
        hashed = hash_password(account.password)
        model = Admin if account.role == "管理员" else User
        await model.filter(id=user.id).update(password=hashed)
        user.password = hashed

    await login_rate_limiter.record_success(
        client_ip,
        account.username,
        account.role,
    )

    session_id = str(uuid.uuid4())
    access_token, refresh_token, refresh_jti = create_token_pair(
        user,
        account.role,
        session_id,
    )
    await AuthSession.create(
        id=session_id,
        user_id=user.id,
        role=account.role,
        refresh_jti=refresh_jti,
        expires_at=datetime.now(timezone.utc)
        + timedelta(days=JWT_REFRESH_EXPIRE_DAYS),
    )
    csrf_token = new_csrf_token()
    set_auth_cookies(response, access_token, refresh_token, csrf_token)
    return Result.success({
        "user": _user_data(user, account.role),
        "csrfToken": csrf_token,
    })


@api_router.post("/refresh")
async def refresh_session(request: Request, response: Response):
    validate_csrf(request)
    refresh_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if not refresh_token:
        raise HTTPException(status_code=401, detail="刷新会话不存在")

    current_user, account, session = await authenticate_token(
        refresh_token,
        "refresh",
    )
    access_token, new_refresh_token, new_refresh_jti = create_token_pair(
        account,
        current_user["role"],
        session.id,
    )
    updated = await AuthSession.filter(
        id=session.id,
        refresh_jti=session.refresh_jti,
        revoked_at__isnull=True,
    ).update(refresh_jti=new_refresh_jti)
    if updated != 1:
        raise HTTPException(status_code=401, detail="刷新会话已被使用或撤销")

    csrf_token = request.cookies.get(CSRF_COOKIE_NAME) or new_csrf_token()
    set_auth_cookies(
        response,
        access_token,
        new_refresh_token,
        csrf_token,
    )
    return Result.success({
        "user": _user_data(account, current_user["role"]),
        "csrfToken": csrf_token,
    })


@api_router.post("/logout")
async def logout(request: Request, response: Response):
    validate_csrf(request)
    token_candidates = (
        (request.cookies.get(ACCESS_COOKIE_NAME), "access"),
        (request.cookies.get(REFRESH_COOKIE_NAME), "refresh"),
    )
    for token, token_type in token_candidates:
        if not token:
            continue
        try:
            _, _, session = await authenticate_token(token, token_type)
            await AuthSession.filter(
                id=session.id,
                revoked_at__isnull=True,
            ).update(revoked_at=datetime.now(timezone.utc))
            break
        except HTTPException:
            continue
    clear_auth_cookies(response)
    return Result.success()


# 注册
@api_router.post("/register")
async def register(account: Account):
    user = await User.get_or_none(username=account.username)
    if user is not None:
        raise CustomException("账号重复")
    if account.name is None:
        account.name = account.username
    if account.password is None:
        account.password = "123"
    create_data = account.model_dump(exclude_unset=True, exclude={'id'})
    create_data['password'] = hash_password(create_data['password'])
    create_data['role'] = '用户'
    await User.create(**create_data)
    return Result.success()


# 验证token是否有效
@api_router.get("/verify")
async def verify_token(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    return Result.success({
        "user": {
            "id": current_user["user_id"],
            "username": current_user["username"],
            "name": current_user["name"],
            "avatar": current_user["avatar"],
            "role": current_user["role"],
        },
        "csrfToken": request.cookies.get(CSRF_COOKIE_NAME),
    })


# 修改密码
@api_router.put("/updatePassword")
async def update_password(
    password_update: PasswordUpdateRequest,
    response: Response,
    current_user: dict = Depends(get_current_user),
):
    # 修改目标必须来自已验证 JWT，不能信任请求体中的 id 和 role。
    role = current_user.get("role")
    user_id = current_user.get("user_id")
    if role == '管理员':
        model = Admin
    elif role == '用户':
        model = User
    else:
        raise HTTPException(status_code=403, detail="无权修改密码")

    user = await model.get_or_none(id=user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="当前账号不存在或已失效")

    if not password_update.password or not password_update.newPassword:
        raise CustomException("原密码和新密码不能为空")

    is_valid, _ = verify_password(password_update.password, user.password)
    if not is_valid:
        raise CustomException("原密码错误")
    if verify_password(password_update.newPassword, user.password)[0]:
        raise CustomException("新密码不能跟原密码相同")

    now = datetime.now(timezone.utc)
    updated = await model.filter(
        id=user_id,
        token_version=current_user["token_version"],
    ).update(
        password=hash_password(password_update.newPassword),
        token_version=current_user["token_version"] + 1,
    )
    if updated != 1:
        raise HTTPException(status_code=409, detail="账号状态已变化，请重新登录")
    await AuthSession.filter(
        user_id=user_id,
        role=role,
        revoked_at__isnull=True,
    ).update(revoked_at=now)
    clear_auth_cookies(response)
    return Result.success()


# 自动导入当前目录下的所有模块
for _, module_name, _ in pkgutil.iter_modules(__path__, __name__ + "."):
    module = importlib.import_module(module_name)
    if hasattr(module, "router"):
        api_router.include_router(module.router)
