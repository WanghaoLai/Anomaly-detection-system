import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


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


if __name__ == "__main__":
    unittest.main()
