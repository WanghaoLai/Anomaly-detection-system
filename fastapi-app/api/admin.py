from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import create_model
from tortoise.contrib.pydantic import pydantic_model_creator

from common.auth import get_current_admin, hash_password
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


@router.post("/add")
async def add(admin_create_pydantic: AdminCreatePydantic):
    admin = await Admin.get_or_none(username=admin_create_pydantic.username)
    if admin is not None:
        raise CustomException("账号重复")
    if admin_create_pydantic.name is None:
        admin_create_pydantic.name = admin_create_pydantic.username
    if admin_create_pydantic.password is None:
        admin_create_pydantic.password = "admin"
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
    update_data.pop('token_version', None)

    admin = await Admin.get_or_none(id=admin_create_pydantic.id)
    if admin is None:
        raise CustomException("未找到管理员")
    password_changed = bool(
        update_data.get('password')
        and update_data.get('password') != admin.password
    )
    if not password_changed:
        update_data.pop('password', None)
    else:
        update_data['password'] = hash_password(update_data['password'])
        update_data['token_version'] = admin.token_version + 1
    query = Admin.filter(id=admin_create_pydantic.id)
    if password_changed:
        query = query.filter(token_version=admin.token_version)
    updated = await query.update(**update_data)
    if updated != 1:
        raise CustomException("管理员状态已变化，请重试")
    if password_changed:
        await AuthSession.filter(
            user_id=admin.id,
            role="管理员",
            revoked_at__isnull=True,
        ).update(revoked_at=datetime.now(timezone.utc))
    return Result.success()


@router.delete("/delete/{admin_id}")
async def delete(admin_id: int):
    await AuthSession.filter(user_id=admin_id, role="管理员").delete()
    await Admin.filter(id=admin_id).delete()
    return Result.success()


@router.delete("/deleteBatch")
async def delete_batch(ids: List[int]):
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
async def select_page(name: str = "", pageNum: int = 1, pageSize: int = 10):
    query = Admin.filter(name__contains=name)
    admin_list = await query.offset((pageNum - 1) * pageSize).limit(pageSize)
    admin_list = [
        AdminReadPydantic.model_validate(admin).model_dump()
        for admin in admin_list
    ]
    total = await query.count()
    pageinfo = PageInfo(total=total, list=admin_list)
    return Result.success(pageinfo)
