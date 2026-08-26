import logging
import sys
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from common.exception_handler import setup_exceptions  # noqa: E402


def _client() -> TestClient:
    app = FastAPI()
    setup_exceptions(app)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("boom-detail")

    return TestClient(app, raise_server_exceptions=False)


class GlobalExceptionHandlerTests(unittest.TestCase):
    """🟡#4 回归：未处理异常必须带堆栈写入日志，响应契约保持 200 + code=500。"""

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

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"code": "500", "msg": "系统错误"})
        log_output = stream.getvalue()
        self.assertIn("未处理异常: GET /boom", log_output)
        self.assertIn("RuntimeError: boom-detail", log_output)  # 包含完整堆栈


if __name__ == "__main__":
    unittest.main()
