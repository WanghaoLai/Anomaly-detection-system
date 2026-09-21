"""检查或批量迁移历史明文密码。

默认只检查；只有显式传入 ``--apply`` 才会把非 bcrypt 密码原地哈希。
"""

import argparse
import asyncio

from tortoise import Tortoise

from common.auth import hash_password, is_bcrypt_hash, legacy_password_accounts
from models import Admin, User
from settings import TORTOISE_ORM


async def migrate_legacy_passwords(*, apply: bool) -> tuple[int, int]:
    accounts = await legacy_password_accounts()
    if not apply:
        return len(accounts), 0

    migrated = 0
    for account in accounts:
        model = Admin if account["role"] == "管理员" else User
        row = await model.get_or_none(id=account["id"])
        if row is None or not isinstance(row.password, str) or is_bcrypt_hash(row.password):
            continue
        plaintext = row.password
        updated = await model.filter(id=row.id, password=plaintext).update(
            password=hash_password(plaintext)
        )
        migrated += int(updated == 1)
    return len(accounts), migrated


async def _run(apply: bool) -> int:
    await Tortoise.init(config=TORTOISE_ORM)
    try:
        found, migrated = await migrate_legacy_passwords(apply=apply)
        if apply:
            print(f"检测到 {found} 个遗留账号，已迁移 {migrated} 个")
            return 0 if found == migrated else 1
        print(f"检测到 {found} 个遗留明文密码账号")
        return 2 if found else 0
    finally:
        await Tortoise.close_connections()


def main() -> None:
    parser = argparse.ArgumentParser(description="迁移历史明文密码为 bcrypt")
    parser.add_argument("--apply", action="store_true", help="执行迁移；默认只检查")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args.apply)))


if __name__ == "__main__":
    main()
