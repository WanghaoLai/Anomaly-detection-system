"""自主注册的实时策略；数据库保存管理员选择，环境变量仅提供初始值。"""

from datetime import datetime, timezone

from tortoise.exceptions import IntegrityError

from models import RegistrationPolicy
from settings import SELF_REGISTRATION_ENABLED


async def is_registration_enabled() -> bool:
    policy = await RegistrationPolicy.get_or_none(id=1)
    return policy.enabled if policy is not None else SELF_REGISTRATION_ENABLED


async def set_registration_enabled(enabled: bool, *, admin_id: int) -> bool:
    values = {
        "enabled": enabled,
        "updated_by": admin_id,
        "updated_at": datetime.now(timezone.utc),
    }
    if await RegistrationPolicy.get_or_none(id=1) is None:
        try:
            await RegistrationPolicy.create(id=1, **values)
            return enabled
        except IntegrityError:
            # 两位管理员首次设置时，固定主键保证只有一行。
            pass
    await RegistrationPolicy.filter(id=1).update(**values)
    return enabled
