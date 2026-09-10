import sys
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

import api.files as files_api  # noqa: E402
from api.files import router  # noqa: E402
from fastapi import HTTPException  # noqa: E402


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class FilesDownloadAuthTests(unittest.TestCase):
    """🔴#1 回归：上传产物下载必须经过认证，未登录请求返回 401。"""

    def test_download_without_credentials_is_unauthorized(self):
        response = _client().get("/files/download/avatars/head.jpg")
        self.assertEqual(response.status_code, 401)

    def test_download_with_forged_cookie_only_is_unauthorized(self):
        # 伪造的 Access Cookie 无法通过签名与会话校验，同样拒绝。
        client = _client()
        client.cookies.set("access_token", "not-a-valid-jwt")
        response = client.get("/files/download/inference/20260822_deadbeef.png")
        self.assertEqual(response.status_code, 401)

    def test_upload_route_is_unchanged_and_also_requires_auth(self):
        response = _client().post("/files/upload")
        self.assertEqual(response.status_code, 401)

    def _download(self, record, current_user):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / record.relative_path
            target.parent.mkdir(parents=True)
            target.write_bytes(b"image")
            with (
                patch.object(files_api, "UPLOAD_DIR", root),
                patch.object(
                    files_api.StoredFile,
                    "get_or_none",
                    new=AsyncMock(return_value=record),
                ),
            ):
                return asyncio.run(files_api.download_file(record.id, current_user))

    def test_owner_can_download_owner_scoped_file(self):
        record = SimpleNamespace(
            id="file-1", relative_path="images/a.png", original_name="a.png",
            media_type="image/png", access_scope="OWNER", owner_id=7, owner_role="用户",
        )
        response = self._download(record, {"user_id": 7, "role": "用户"})
        self.assertEqual(Path(response.path).name, "a.png")

    def test_other_user_cannot_download_owner_scoped_file(self):
        record = SimpleNamespace(
            id="file-2", relative_path="inference/result.png", original_name="result.png",
            media_type="image/png", access_scope="OWNER", owner_id=7, owner_role="用户",
        )
        with self.assertRaises(HTTPException) as raised:
            self._download(record, {"user_id": 8, "role": "用户"})
        self.assertEqual(raised.exception.status_code, 404)

    def test_admin_can_download_owner_scoped_file(self):
        record = SimpleNamespace(
            id="file-3", relative_path="inference/result.png", original_name="result.png",
            media_type="image/png", access_scope="OWNER", owner_id=7, owner_role="用户",
        )
        response = self._download(record, {"user_id": 1, "role": "管理员"})
        self.assertEqual(Path(response.path).name, "result.png")

    def test_any_authenticated_user_can_read_avatar(self):
        record = SimpleNamespace(
            id="file-4", relative_path="avatars/head.png", original_name="head.png",
            media_type="image/png", access_scope="AUTHENTICATED", owner_id=7, owner_role="用户",
        )
        response = self._download(record, {"user_id": 8, "role": "用户"})
        self.assertEqual(Path(response.path).name, "head.png")


if __name__ == "__main__":
    unittest.main()
