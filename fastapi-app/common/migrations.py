"""MySQL schema migration discovery, execution and startup validation."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from settings import BASE_DIR, TORTOISE_ORM


MIGRATION_PATTERN = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")
MIGRATION_LOCK_NAME = "ad_system_schema_migrations"
HISTORY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS `schema_migrations` (
  `version` int NOT NULL,
  `name` varchar(255) NOT NULL,
  `checksum` char(64) NOT NULL,
  `status` varchar(16) NOT NULL,
  `error_message` varchar(1000) DEFAULT NULL,
  `started_at` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `applied_at` datetime(6) DEFAULT NULL,
  PRIMARY KEY (`version`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""".strip()


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path
    sql: str
    checksum: str


def discover_migrations(directory: Path | None = None) -> list[Migration]:
    root = directory or BASE_DIR / "migrations"
    migrations: list[Migration] = []
    for path in sorted(root.glob("*.sql")):
        match = MIGRATION_PATTERN.fullmatch(path.name)
        if not match:
            raise MigrationError(f"迁移文件名不合法: {path.name}")
        raw = path.read_bytes()
        sql = raw.decode("utf-8")
        migrations.append(Migration(
            version=int(match.group(1)),
            name=path.name,
            path=path,
            sql=sql,
            checksum=hashlib.sha256(raw).hexdigest(),
        ))
    if not migrations:
        raise MigrationError(f"未找到迁移文件: {root}")
    versions = [item.version for item in migrations]
    expected = list(range(1, versions[-1] + 1))
    if versions != expected:
        raise MigrationError(f"迁移版本必须从 001 连续递增，实际为: {versions}")
    return migrations


def split_sql_statements(sql: str) -> list[str]:
    """在不破坏引号内分号的前提下分割当前项目的普通 DDL/DML 脚本。"""
    statements: list[str] = []
    current: list[str] = []
    quote: str | None = None
    line_comment = False
    block_comment = False
    index = 0
    while index < len(sql):
        char = sql[index]
        nxt = sql[index + 1] if index + 1 < len(sql) else ""
        if line_comment:
            if char == "\n":
                line_comment = False
                current.append(char)
            index += 1
            continue
        if block_comment:
            if char == "*" and nxt == "/":
                block_comment = False
                index += 2
            else:
                index += 1
            continue
        if quote:
            current.append(char)
            if char == "\\" and quote in {"'", '"'} and nxt:
                current.append(nxt)
                index += 2
                continue
            if char == quote:
                if nxt == quote:
                    current.append(nxt)
                    index += 2
                    continue
                quote = None
            index += 1
            continue
        if char == "#" or (char == "-" and nxt == "-" and (
            index + 2 == len(sql) or sql[index + 2].isspace()
        )):
            line_comment = True
            index += 2 if char == "-" else 1
            continue
        if char == "/" and nxt == "*":
            block_comment = True
            index += 2
            continue
        if char in {"'", '"', "`"}:
            quote = char
            current.append(char)
        elif char == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
        else:
            current.append(char)
        index += 1
    statement = "".join(current).strip()
    if statement:
        statements.append(statement)
    if quote or block_comment:
        raise MigrationError("SQL 迁移包含未闭合的引号或注释")
    return statements


def _connection_kwargs() -> dict[str, Any]:
    credentials = dict(TORTOISE_ORM["connections"]["default"]["credentials"])
    return {
        "host": credentials["host"],
        "port": credentials["port"],
        "user": credentials["user"],
        "password": credentials["password"],
        "db": credentials["database"],
        "charset": credentials.get("charset", "utf8mb4"),
        "autocommit": True,
    }


async def _open_connection():
    try:
        import aiomysql
    except ImportError as exc:
        raise MigrationError("缺少 aiomysql，请先安装项目依赖") from exc
    return await aiomysql.connect(**_connection_kwargs())


async def _fetch_history(cursor, *, require_table: bool) -> dict[int, dict[str, Any]]:
    try:
        await cursor.execute(
            "SELECT version, name, checksum, status, error_message "
            "FROM schema_migrations ORDER BY version"
        )
    except Exception as exc:
        if require_table:
            raise MigrationError(
                "未建立迁移历史。旧库请先根据已实际落库的最高版本执行 "
                "`python manage_migrations.py baseline --version <N>` 再 apply；"
                "仅含原始基础表的库直接执行 `python manage_migrations.py apply`"
            ) from exc
        return {}
    rows = await cursor.fetchall()
    return {
        int(row[0]): {
            "name": row[1], "checksum": row[2], "status": row[3], "error": row[4]
        }
        for row in rows
    }


def _validate_history(migrations: Iterable[Migration], history: dict[int, dict[str, Any]]) -> None:
    expected = {item.version: item for item in migrations}
    unknown = sorted(set(history) - set(expected))
    if unknown:
        raise MigrationError(f"数据库含本版本代码不认识的迁移: {unknown}")
    for version, applied in history.items():
        migration = expected[version]
        if applied["name"] != migration.name or applied["checksum"] != migration.checksum:
            raise MigrationError(
                f"已执行迁移 {version:03d} 的名称或校验和被修改，拒绝启动"
            )
        if applied["status"] != "APPLIED":
            raise MigrationError(
                f"迁移 {version:03d} 状态为 {applied['status']}: {applied['error'] or ''}"
            )


async def check_schema_current() -> None:
    migrations = discover_migrations()
    connection = await _open_connection()
    try:
        cursor = await connection.cursor()
        try:
            history = await _fetch_history(cursor, require_table=True)
        finally:
            await cursor.close()
    finally:
        connection.close()
    _validate_history(migrations, history)
    pending = [item.name for item in migrations if item.version not in history]
    if pending:
        raise MigrationError(
            "数据库存在未执行迁移: " + ", ".join(pending)
            + "；请先执行 `python manage_migrations.py apply`"
        )


async def apply_migrations() -> list[str]:
    migrations = discover_migrations()
    connection = await _open_connection()
    applied_names: list[str] = []
    cursor = await connection.cursor()
    locked = False
    try:
        await cursor.execute("SELECT GET_LOCK(%s, %s)", (MIGRATION_LOCK_NAME, 30))
        row = await cursor.fetchone()
        if not row or row[0] != 1:
            raise MigrationError("无法在 30 秒内获得数据库迁移锁")
        locked = True
        await cursor.execute(HISTORY_TABLE_SQL)
        history = await _fetch_history(cursor, require_table=True)
        _validate_history(migrations, history)
        for migration in migrations:
            if migration.version in history:
                continue
            await cursor.execute(
                "INSERT INTO schema_migrations "
                "(version, name, checksum, status) VALUES (%s, %s, %s, 'APPLYING')",
                (migration.version, migration.name, migration.checksum),
            )
            # 预先绑定：split_sql_statements 在迭代开始前求值，若迁移文件
            # 含未闭合引号/注释，循环变量从未赋值，except 分支引用它会抛
            # NameError，导致 FAILED 状态永不落库。
            statement_number = 0
            try:
                for statement_number, statement in enumerate(
                    split_sql_statements(migration.sql), start=1
                ):
                    await cursor.execute(statement)
            except Exception as exc:
                if statement_number:
                    message = f"第 {statement_number} 条 SQL 失败: {exc}"[:1000]
                else:
                    # 尚未执行任何 SQL，失败发生在语句切分阶段。
                    message = f"SQL 语句切分失败: {exc}"[:1000]
                await cursor.execute(
                    "UPDATE schema_migrations SET status='FAILED', error_message=%s "
                    "WHERE version=%s",
                    (message, migration.version),
                )
                raise MigrationError(
                    f"迁移 {migration.name} 执行失败。MySQL DDL 可能已部分提交，"
                    "请核对表结构后人工修复 FAILED 记录"
                ) from exc
            await cursor.execute(
                "UPDATE schema_migrations SET status='APPLIED', error_message=NULL, "
                "applied_at=CURRENT_TIMESTAMP(6) WHERE version=%s",
                (migration.version,),
            )
            applied_names.append(migration.name)
    finally:
        if locked:
            try:
                await cursor.execute("SELECT RELEASE_LOCK(%s)", (MIGRATION_LOCK_NAME,))
            except Exception:
                pass
        await cursor.close()
        connection.close()
    return applied_names


async def baseline(version: int) -> list[str]:
    migrations = discover_migrations()
    if version < 0 or version > migrations[-1].version:
        raise MigrationError(f"基线版本必须介于 0 与 {migrations[-1].version} 之间")
    connection = await _open_connection()
    cursor = await connection.cursor()
    locked = False
    try:
        await cursor.execute("SELECT GET_LOCK(%s, %s)", (MIGRATION_LOCK_NAME, 30))
        row = await cursor.fetchone()
        if not row or row[0] != 1:
            raise MigrationError("无法在 30 秒内获得数据库迁移锁")
        locked = True
        await cursor.execute(HISTORY_TABLE_SQL)
        history = await _fetch_history(cursor, require_table=True)
        if history:
            raise MigrationError("迁移历史已存在，拒绝重新设置基线")
        selected = [item for item in migrations if item.version <= version]
        for item in selected:
            await cursor.execute(
                "INSERT INTO schema_migrations "
                "(version, name, checksum, status, applied_at) "
                "VALUES (%s, %s, %s, 'APPLIED', CURRENT_TIMESTAMP(6))",
                (item.version, item.name, item.checksum),
            )
        return [item.name for item in selected]
    finally:
        if locked:
            try:
                await cursor.execute("SELECT RELEASE_LOCK(%s)", (MIGRATION_LOCK_NAME,))
            except Exception:
                pass
        await cursor.close()
        connection.close()


async def migration_status() -> list[tuple[Migration, str]]:
    migrations = discover_migrations()
    connection = await _open_connection()
    try:
        cursor = await connection.cursor()
        try:
            history = await _fetch_history(cursor, require_table=False)
        finally:
            await cursor.close()
    finally:
        connection.close()
    _validate_history(migrations, history)
    return [(item, history.get(item.version, {}).get("status", "PENDING")) for item in migrations]


def run_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ad_system 数据库迁移管理")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="显示所有迁移状态")
    subparsers.add_parser("check", help="校验数据库是否与代码同版")
    subparsers.add_parser("apply", help="在 MySQL 建议锁下顺序执行待迁移版本")
    baseline_parser = subparsers.add_parser(
        "baseline", help="仅为已具备相应结构的旧库建立迁移基线"
    )
    baseline_parser.add_argument("--version", required=True, help="版本数或 latest")
    args = parser.parse_args(argv)
    try:
        if args.command == "status":
            for migration, status in asyncio.run(migration_status()):
                print(f"{migration.version:03d}  {status:<8}  {migration.name}")
        elif args.command == "check":
            asyncio.run(check_schema_current())
            print("数据库迁移已是最新版本")
        elif args.command == "apply":
            applied = asyncio.run(apply_migrations())
            print("已执行: " + (", ".join(applied) if applied else "无（已是最新版）"))
        else:
            latest = discover_migrations()[-1].version
            version = latest if args.version == "latest" else int(args.version)
            stamped = asyncio.run(baseline(version))
            print("已建立基线: " + (", ".join(stamped) if stamped else "0"))
        return 0
    except (MigrationError, ValueError) as exc:
        parser.exit(1, f"迁移失败: {exc}\n")
