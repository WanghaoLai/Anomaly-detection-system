from datetime import UTC, datetime

from common.auth import (
    get_current_admin,
    hash_password,
    validate_password_policy,
)
from common.exception_handler import CustomException
from common.registration_policy import is_registration_enabled, set_registration_enabled
from common.result import PageInfo, Result
from fastapi import APIRouter, Depends, Query
from models import (
    Admin,
    Algorithm,
    AuthSession,
    Dataset,
    InferenceJob,
    TrainingJob,
)
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator
from tortoise.contrib.pydantic import pydantic_model_creator
from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

router = APIRouter(prefix="/admin", dependencies=[Depends(get_current_admin)])
AdminPydantic = pydantic_model_creator(Admin)
AdminReadPydantic = pydantic_model_creator(
    Admin,
    exclude=("password", "token_version"),
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AdminCreatePydantic(_StrictModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=255)
    name: str | None = Field(default=None, max_length=255)
    avatar: str | None = Field(default=None, max_length=255)


class AdminUpdatePydantic(_StrictModel):
    id: int = Field(gt=0)
    username: str = Field(default=None, min_length=1, max_length=255)
    name: str | None = Field(default=None, max_length=255)
    avatar: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def require_change(self):
        if not (self.model_fields_set - {"id"}):
            raise ValueError("至少提供一个需要更新的字段")
        return self


class AdminPasswordResetRequest(_StrictModel):
    newPassword: str = Field(min_length=1, max_length=255)


class RegistrationPolicyUpdate(_StrictModel):
    enabled: StrictBool


@router.get("/registration-policy")
async def get_registration_policy():
    return Result.success({"enabled": await is_registration_enabled()})


@router.put("/registration-policy")
async def update_registration_policy(
    policy: RegistrationPolicyUpdate,
    current_admin: dict = Depends(get_current_admin),
):
    enabled = await set_registration_enabled(
        policy.enabled,
        admin_id=current_admin["user_id"],
    )
    return Result.success({"enabled": enabled})


@router.post("/add")
async def add(admin_create_pydantic: AdminCreatePydantic):
    admin = await Admin.get_or_none(username=admin_create_pydantic.username)
    if admin is not None:
        raise CustomException("账号重复", status_code=409)
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
    try:
        await Admin.create(**create_data)
    except IntegrityError as exc:
        raise CustomException("账号重复", status_code=409) from exc
    return Result.success()


@router.put("/update")
async def update(admin_create_pydantic: AdminUpdatePydantic):
    update_data = admin_create_pydantic.model_dump(exclude_unset=True, exclude={'id'})
    admin = await Admin.get_or_none(id=admin_create_pydantic.id)
    if admin is None:
        raise CustomException("未找到管理员", status_code=404)
    try:
        updated = await Admin.filter(id=admin.id).update(**update_data)
    except IntegrityError as exc:
        raise CustomException("账号重复", status_code=409) from exc
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
        raise CustomException("未找到管理员", status_code=404)

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
    ).update(revoked_at=datetime.now(UTC))
    return Result.success()


@router.delete("/delete/{admin_id}")
async def delete(
    admin_id: int,
    current_admin: dict = Depends(get_current_admin),
):
    # 删除自己会立即吊销当前会话，把操作者锁在系统外。
    if admin_id == current_admin["user_id"]:
        raise CustomException("不能删除当前登录的管理员账号")

    try:
        async with in_transaction() as connection:
            admin = await Admin.filter(id=admin_id).using_db(
                connection
            ).select_for_update().first()
            if admin is None:
                raise CustomException("未找到管理员", status_code=404)

            if await Algorithm.filter(created_by_id=admin_id).using_db(
                connection
            ).exists():
                raise CustomException(
                    "该管理员仍有关联算法，请先转移或删除相关算法",
                    status_code=409,
                )
            if await Dataset.filter(created_by_id=admin_id).using_db(
                connection
            ).exists():
                raise CustomException(
                    "该管理员仍有关联数据集，请先转移或删除相关数据集",
                    status_code=409,
                )
            if await TrainingJob.filter(
                owner_id=admin_id,
                owner_role="管理员",
            ).using_db(connection).exists():
                raise CustomException(
                    "该管理员仍有关联训练任务，请先转移或清理相关任务",
                    status_code=409,
                )
            if await InferenceJob.filter(
                owner_id=admin_id,
                owner_role="管理员",
            ).using_db(connection).exists():
                raise CustomException(
                    "该管理员仍有关联推理任务，请先转移或清理相关任务",
                    status_code=409,
                )

            await AuthSession.filter(
                user_id=admin_id,
                role="管理员",
            ).using_db(connection).delete()
            deleted = await Admin.filter(id=admin_id).using_db(connection).delete()
            if deleted != 1:
                raise CustomException("管理员状态已变化，请重试", status_code=409)
    except IntegrityError as exc:
        # 目标行锁可阻止大多数并发新增引用；外键约束仍作为最终兜底。
        raise CustomException(
            "该管理员仍有关联数据，暂时无法删除",
            status_code=409,
        ) from exc
    return Result.success()


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
