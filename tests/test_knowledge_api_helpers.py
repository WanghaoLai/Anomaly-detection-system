import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tortoise import Tortoise, connections

BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

SPEC = importlib.util.spec_from_file_location(
    "knowledge_api_direct",
    BACKEND_DIR / "api" / "knowledge.py",
)
knowledge_api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(knowledge_api)


class FakeUpload:
    def __init__(self, content: bytes):
        self.content = content
        self.offset = 0

    async def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.content) - self.offset
        chunk = self.content[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk


class KnowledgeUploadLimitTests(unittest.TestCase):
    def test_backend_accepts_file_at_limit(self):
        with patch.object(knowledge_api, "MAX_UPLOAD_BYTES", 4):
            result = asyncio.run(
                knowledge_api._read_upload_limited(FakeUpload(b"1234"))
            )
        self.assertEqual(result, b"1234")

    def test_backend_rejects_file_over_limit(self):
        with patch.object(knowledge_api, "MAX_UPLOAD_BYTES", 4):
            with self.assertRaisesRegex(Exception, "文件大小不能超过"):
                asyncio.run(
                    knowledge_api._read_upload_limited(FakeUpload(b"12345"))
                )


class KnowledgeReleaseRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(
            db_url="sqlite://:memory:",
            modules={"models": ["models"]},
        )
        self.addAsyncCleanup(self._shutdown_db)
        await Tortoise.generate_schemas()

    @staticmethod
    async def _shutdown_db():
        await connections.close_all(discard=True)
        await Tortoise._reset_apps()

    async def _operation(self, release_id="release-1"):
        return await knowledge_api.KnowledgeReleaseOperation.create(
            release_id=release_id,
            operation="UPLOAD",
            status="PENDING",
            payload_json={"original_name": "manual.pdf", "previous_rows": []},
        )

    async def test_pending_release_is_published_then_marked_durable(self):
        operation = await self._operation()
        fake = Mock()
        fake.current_release_id.return_value = "legacy:documents"

        with patch.object(knowledge_api, "knowledge_service", fake):
            await knowledge_api._publish_recorded_release(operation)

        fake.publish_staged_release.assert_called_once_with("release-1")
        saved = await knowledge_api.KnowledgeReleaseOperation.get(
            release_id="release-1"
        )
        self.assertEqual(saved.status, "PUBLISHED")

    async def test_crash_after_pointer_switch_is_recovered_idempotently(self):
        operation = await self._operation()
        fake = Mock()
        fake.current_release_id.return_value = "release-1"

        with patch.object(knowledge_api, "knowledge_service", fake):
            await knowledge_api._publish_recorded_release(operation)

        fake.publish_staged_release.assert_not_called()
        saved = await knowledge_api.KnowledgeReleaseOperation.get(
            release_id="release-1"
        )
        self.assertEqual(saved.status, "PUBLISHED")

    async def test_startup_fails_closed_when_pending_release_cannot_publish(self):
        await self._operation()
        fake = Mock()
        fake.current_release_id.return_value = "legacy:documents"
        fake.publish_staged_release.side_effect = RuntimeError("missing manifest")

        with patch.object(knowledge_api, "knowledge_service", fake):
            with self.assertRaisesRegex(RuntimeError, "拒绝启动"):
                await knowledge_api.recover_pending_knowledge_releases()

        saved = await knowledge_api.KnowledgeReleaseOperation.get(
            release_id="release-1"
        )
        self.assertEqual(saved.status, "PENDING")
        self.assertIn("missing manifest", saved.error_message)


if __name__ == "__main__":
    unittest.main()
