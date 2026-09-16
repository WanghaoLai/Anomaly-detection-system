import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from common.migrations import (  # noqa: E402
    MigrationError,
    discover_migrations,
    split_sql_statements,
)


class MigrationDiscoveryTests(unittest.TestCase):
    def test_project_migrations_are_contiguous_and_checksummed(self):
        migrations = discover_migrations(BACKEND_DIR / "migrations")
        self.assertEqual([item.version for item in migrations], list(range(1, 19)))
        for item in migrations:
            self.assertEqual(
                item.checksum,
                hashlib.sha256(item.path.read_bytes()).hexdigest(),
            )

    def test_gap_in_versions_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "001_first.sql").write_text("SELECT 1;", encoding="utf-8")
            (root / "003_third.sql").write_text("SELECT 3;", encoding="utf-8")
            with self.assertRaises(MigrationError):
                discover_migrations(root)

    def test_splitter_preserves_semicolons_in_literals_and_backticks(self):
        sql = """
        -- ignored ; comment
        INSERT INTO `odd;table` (`value`) VALUES ('a;b');
        UPDATE `odd;table` SET `value` = "c;d";
        """
        statements = split_sql_statements(sql)
        self.assertEqual(len(statements), 2)
        self.assertIn("'a;b'", statements[0])
        self.assertIn('"c;d"', statements[1])


class _FakeCursor:
    """按 apply_migrations 真实调用序列返回结果的记录型游标。"""

    def __init__(self):
        self.executed: list[tuple[str, object]] = []
        self._fetchone_result = None
        self._fetchall_result: list = []
        self.closed = False

    async def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if sql.startswith("SELECT GET_LOCK"):
            self._fetchone_result = (1,)
        elif sql.startswith("SELECT version, name"):
            self._fetchall_result = []

    async def fetchone(self):
        return self._fetchone_result

    async def fetchall(self):
        return self._fetchall_result

    async def close(self):
        self.closed = True


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor
        self.closed = False

    async def cursor(self) -> _FakeCursor:
        return self._cursor

    def close(self):
        self.closed = True


class ApplyMigrationFailureTests(unittest.TestCase):
    def test_split_failure_records_failed_status_without_name_error(self):
        # 迁移文件含未闭合引号时，语句切分在 for 循环开始前抛
        # MigrationError；失败分支曾引用未绑定的 statement_number 抛
        # NameError，导致 FAILED 状态永不落库、历史行卡在 APPLYING。
        import asyncio
        from unittest import mock

        from common import migrations as migrations_module

        bad_migration = migrations_module.Migration(
            version=1,
            name="001_bad.sql",
            path=Path("001_bad.sql"),
            sql="INSERT INTO `t` VALUES ('a;",
            checksum="0" * 64,
        )
        cursor = _FakeCursor()

        async def fake_open_connection():
            return _FakeConnection(cursor)

        with (
            mock.patch.object(
                migrations_module, "discover_migrations", return_value=[bad_migration]
            ),
            mock.patch.object(
                migrations_module, "_open_connection", new=fake_open_connection
            ),
        ):
            with self.assertRaises(migrations_module.MigrationError):
                asyncio.run(migrations_module.apply_migrations())

        failed_updates = [
            params
            for sql, params in cursor.executed
            if sql.startswith("UPDATE schema_migrations SET status='FAILED'")
        ]
        self.assertEqual(len(failed_updates), 1)
        self.assertIn("SQL 语句切分失败", failed_updates[0][0])
        # finally 分支正常执行：释放锁并关闭游标。
        self.assertTrue(cursor.closed)

    def test_statement_failure_reports_statement_number(self):
        # 第 N 条 SQL 执行失败时，错误消息必须带上具体条数。
        import asyncio
        from unittest import mock

        from common import migrations as migrations_module

        class _ExplodingCursor(_FakeCursor):
            async def execute(self, sql, params=None):
                if sql.startswith("ALTER TABLE"):
                    raise RuntimeError("syntax error near ';'")
                return await super().execute(sql, params)

        migration = migrations_module.Migration(
            version=1,
            name="001_two_statements.sql",
            path=Path("001_two_statements.sql"),
            sql="SELECT 1; ALTER TABLE `t` ADD COLUMN c INT;",
            checksum="0" * 64,
        )
        cursor = _ExplodingCursor()

        async def fake_open_connection():
            return _FakeConnection(cursor)

        with (
            mock.patch.object(
                migrations_module, "discover_migrations", return_value=[migration]
            ),
            mock.patch.object(
                migrations_module, "_open_connection", new=fake_open_connection
            ),
        ):
            with self.assertRaises(migrations_module.MigrationError):
                asyncio.run(migrations_module.apply_migrations())

        failed_updates = [
            params
            for sql, params in cursor.executed
            if sql.startswith("UPDATE schema_migrations SET status='FAILED'")
        ]
        self.assertEqual(len(failed_updates), 1)
        self.assertIn("第 2 条 SQL 失败", failed_updates[0][0])
        self.assertIn("syntax error", failed_updates[0][0])


if __name__ == "__main__":
    unittest.main()
