from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import create_model
from tortoise.contrib.pydantic import pydantic_model_creator

from common.auth import get_current_admin, get_current_user, hash_password
from common.exception_handler import CustomException
from common.result import Result, PageInfo
from models import AuthSession, User

router = APIRouter(prefix="/user", dependencies=[Depends(get_current_user)])
UserPydantic = pydantic_model_creator(User)
UserReadPydantic = pydantic_model_creator(
    User,
    exclude=("password", "token_version"),
)
UserCreatePydantic = create_model(
    "UserPydantic",
    **{
        name: (Optional[field.annotation], None)
        for name, field in UserPydantic.model_fields.items()
    }
)


@router.post("/add", dependencies=[Depends(get_current_admin)])
async def add(user_pydantic: UserCreatePydantic):
    user = await User.get_or_none(username=user_pydantic.username)
    if user is not None:
        raise CustomException("账号重复")
    if user_pydantic.name is None:
        user_pydantic.name = user_pydantic.username
    if user_pydantic.password is None:
        user_pydantic.password = "123"
    create_data = user_pydantic.model_dump(exclude_unset=True, exclude={'id'})
    create_data.pop('token_version', None)
    create_data['password'] = hash_password(create_data['password'])
    create_data['role'] = '用户'
    await User.create(**create_data)
    return Result.success()


@router.put("/update")
async def update(
    user_pydantic: UserCreatePydantic,
    current_user: dict = Depends(get_current_user),
):
    is_admin = current_user["role"] == "管理员"
    if not is_admin and current_user["user_id"] != user_pydantic.id:
        raise HTTPException(status_code=403, detail="无权修改其他用户")

    update_data = user_pydantic.model_dump(exclude_unset=True, exclude={'id'})
    # User 表中的角色是服务端不变量；密码只能通过验证原密码的专用接口修改。
    # 管理员新增用户时仍可设置初始密码，但通用资料更新不能充当密码重置接口。
    update_data.pop('role', None)
    update_data.pop('password', None)
    update_data.pop('token_version', None)

    user = await User.get_or_none(id=user_pydantic.id)
    if user is None:
        raise CustomException("未找到用户")
    await User.filter(id=user_pydantic.id).update(**update_data)
    return Result.success()


@router.delete("/delete/{user_id}", dependencies=[Depends(get_current_admin)])
async def delete(user_id: int):
    await AuthSession.filter(user_id=user_id, role="用户").delete()
    await User.filter(id=user_id).delete()
    return Result.success()


@router.get("/selectPage", dependencies=[Depends(get_current_admin)])
async def select(name: str = "", pageNum: int = 1, pageSize: int = 5):
    query = User.filter(name__contains=name)
    user_list = await query.offset((pageNum - 1) * pageSize).limit(pageSize)
    user_list = [
        UserReadPydantic.model_validate(user).model_dump()
        for user in user_list
    ]
    total = await query.count()
    pageinfo = PageInfo(total=total, list=user_list)
    return Result.success(pageinfo)
