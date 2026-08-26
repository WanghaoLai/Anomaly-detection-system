from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, create_model
from tortoise.contrib.pydantic import pydantic_model_creator

from common.auth import (
    get_current_admin,
    hash_password,
    validate_password_policy,
)
from common.exception_handler import CustomException
from common.result import Result, PageInfo
from models import Admin, AuthSession

router = APIRouter(prefix="/admin", dependencies=[Depends(get_current_admin)])
AdminPydantic = pydantic_model_creator(Admin)
AdminReadPydantic = pydantic_model_creator(
    Admin,
    exclude=("password", "token_version"),
)
AdminCreatePydantic = create_model(
    "AdminPydantic",
    **{
        name: (Optional[field.annotation], None)
        for name, field in AdminPydantic.model_fields.items()
    }
)


class AdminPasswordResetRequest(BaseModel):
    newPassword: str


@router.post("/add")
async def add(admin_create_pydantic: AdminCreatePydantic):
    admin = await Admin.get_or_none(username=admin_create_pydantic.username)
    if admin is not None:
        raise CustomException("账号重复")
    if admin_create_pydantic.name is None:
        admin_create_pydantic.name = admin_create_pydantic.username
    if (
        not admin_create_pydantic.password
        or not admin_create_pydantic.password.strip()
    ):
        raise CustomException("请输入初始密码")
    validate_password_policy(admin_create_pydantic.password)
    create_data = admin_create_pydantic.model_dump(exclude_unset=True, exclude={'id'})
    create_data.pop('token_version', None)
    create_data['password'] = hash_password(create_data['password'])
    create_data['role'] = '管理员'
    await Admin.create(**create_data)
    return Result.success()


@router.put("/update")
async def update(admin_create_pydantic: AdminCreatePydantic):
    update_data = admin_create_pydantic.model_dump(exclude_unset=True, exclude={'id'})
    # Admin 表中的账号角色是服务端不变量，不能由请求体修改。
    update_data.pop('role', None)
    # 密码只允许通过专用重置接口修改，避免通用资料更新误哈希或重复哈希。
    update_data.pop('password', None)
    update_data.pop('token_version', None)

    admin = await Admin.get_or_none(id=admin_create_pydantic.id)
    if admin is None:
        raise CustomException("未找到管理员")
    updated = await Admin.filter(id=admin.id).update(**update_data)
    if updated != 1:
        raise CustomException("管理员状态已变化，请重试")
    return Result.success()


@router.put("/resetPassword/{admin_id}")
async def reset_password(
    admin_id: int,
    password_reset: AdminPasswordResetRequest,
):
    if not password_reset.newPassword or not password_reset.newPassword.strip():
        raise CustomException("新密码不能为空")
    validate_password_policy(password_reset.newPassword)

    admin = await Admin.get_or_none(id=admin_id)
    if admin is None:
        raise CustomException("未找到管理员")

    updated = await Admin.filter(
        id=admin.id,
        token_version=admin.token_version,
    ).update(
        password=hash_password(password_reset.newPassword),
        token_version=admin.token_version + 1,
    )
    if updated != 1:
        raise CustomException("管理员状态已变化，请重试")

    await AuthSession.filter(
        user_id=admin.id,
        role="管理员",
        revoked_at__isnull=True,
    ).update(revoked_at=datetime.now(timezone.utc))
    return Result.success()


@router.delete("/delete/{admin_id}")
async def delete(
    admin_id: int,
    current_admin: dict = Depends(get_current_admin),
):
    # 删除自己会立即吊销当前会话，把操作者锁在系统外。
    if admin_id == current_admin["user_id"]:
        raise CustomException("不能删除当前登录的管理员账号")
    await AuthSession.filter(user_id=admin_id, role="管理员").delete()
    await Admin.filter(id=admin_id).delete()
    return Result.success()


@router.delete("/deleteBatch")
async def delete_batch(
    ids: List[int],
    current_admin: dict = Depends(get_current_admin),
):
    if current_admin["user_id"] in ids:
        raise CustomException("不能删除当前登录的管理员账号")
    await AuthSession.filter(user_id__in=ids, role="管理员").delete()
    await Admin.filter(id__in=ids).delete()
    return Result.success()


@router.get("/selectById/{admin_id}")
async def select_one(admin_id: int):
    admin = await Admin.get(id=admin_id)
    return Result.success(AdminReadPydantic.model_validate(admin).model_dump())


@router.get("/selectAll")
async def select_all(name: str = ""):
    admin_list = await Admin.filter(name__contains=name)
    admin_list = [
        AdminReadPydantic.model_validate(admin).model_dump()
        for admin in admin_list
    ]
    return Result.success(admin_list)


@router.get("/selectPage")
async def select_page(
    name: str = "",
    pageNum: int = Query(1, ge=1),
    pageSize: int = Query(10, ge=1, le=100),
):
    query = Admin.filter(name__contains=name)
    admin_list = await query.offset((pageNum - 1) * pageSize).limit(pageSize)
    admin_list = [
        AdminReadPydantic.model_validate(admin).model_dump()
        for admin in admin_list
    ]
    total = await query.count()
    pageinfo = PageInfo(total=total, list=admin_list)
    return Result.success(pageinfo)
