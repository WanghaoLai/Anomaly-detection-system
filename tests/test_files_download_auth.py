import sys
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from api.files import router  # noqa: E402


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
        response = _client().get(
            "/files/download/inference/20260822_deadbeef.png",
            cookies={"access_token": "not-a-valid-jwt"},
        )
        self.assertEqual(response.status_code, 401)

    def test_upload_route_is_unchanged_and_also_requires_auth(self):
        response = _client().post("/files/upload")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
