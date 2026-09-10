import logging
import sys
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from common.exception_handler import CustomException, setup_exceptions


def _client() -> TestClient:
    app = FastAPI()
    setup_exceptions(app)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("boom-detail")

    @app.get("/conflict")
    async def conflict():
        raise CustomException("资源冲突", status_code=409)

    class Payload(BaseModel):
        value: int = Field(gt=0)

    @app.post("/validate")
    async def validate(payload: Payload):
        return payload

    class Credentials(BaseModel):
        username: str
        password: str
        role: str

    @app.post("/login-like")
    async def login_like(credentials: Credentials):
        return credentials

    return TestClient(app, raise_server_exceptions=False)


class GlobalExceptionHandlerTests(unittest.TestCase):
    """错误响应保留统一响应体，同时使用真实 HTTP 状态。"""

    def test_unhandled_exception_logged_with_traceback_and_returns_contract(self):
        import io

        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("common.exception_handler")
        old_level = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.ERROR)
        try:
            response = _client().get("/boom")
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"code": "500", "msg": "系统错误"})
        log_output = stream.getvalue()
        self.assertIn("未处理异常: GET /boom", log_output)
        self.assertIn("RuntimeError: boom-detail", log_output)  # 包含完整堆栈

    def test_custom_exception_preserves_status(self):
        response = _client().get("/conflict")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"code": "409", "msg": "资源冲突"})

    def test_validation_error_returns_422(self):
        response = _client().post("/validate", json={"value": 0})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json(), {"code": "422", "msg": "请求参数错误"})

    def test_validation_error_log_strips_request_body_with_password(self):
        # Pydantic v2 的 errors() 每条带 input 键（完整原始请求体）。
        # /login 等含密码字段的端点校验失败时，密码绝不能随日志落盘
        # 成为明文口令副本（CWE-532）；诊断信息（类型/字段位置）保留。
        import io

        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("common.exception_handler")
        old_level = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)
        try:
            response = _client().post(
                "/login-like",
                json={"username": "admin", "password": "SuperSecret123"},
            )
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)

        self.assertEqual(response.status_code, 422)
        log_output = stream.getvalue()
        self.assertIn("请求参数校验失败: POST /login-like", log_output)
        self.assertNotIn("SuperSecret123", log_output)
        self.assertNotIn("admin", log_output)
        self.assertIn("missing", log_output)  # 错误类型仍可用于排障
        self.assertIn("role", log_output)  # 出错字段位置仍可用于排障


if __name__ == "__main__":
    unittest.main()
