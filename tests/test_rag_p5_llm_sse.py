import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from services.llm_service import (  # noqa: E402
    LLMGenerationError,
    LLMService,
    LLMTimeoutError,
)
from services.rag.operations.sse import PUBLIC_FAILURE_MESSAGES, encode_sse  # noqa: E402


class LLMAndSSEStateTests(unittest.TestCase):
    def test_llm_timeout_has_typed_status(self):
        service = LLMService("test", timeout_seconds=0.01)

        async def slow(*args, **kwargs):
            await asyncio.sleep(0.05)

        with patch(
            "services.llm_service.LLMService._post",
            new=AsyncMock(side_effect=slow),
        ):
            with self.assertRaises(LLMTimeoutError) as captured:
                asyncio.run(service.chat([{"role": "user", "content": "hi"}]))

        self.assertEqual(captured.exception.code, "llm_timeout")

    def test_generation_sdk_failure_has_typed_status(self):
        service = LLMService("test", timeout_seconds=1)
        with patch(
            "services.llm_service.LLMService._post",
            new=AsyncMock(side_effect=RuntimeError("network down")),
        ):
            with self.assertRaises(LLMGenerationError) as captured:
                asyncio.run(service.chat([{"role": "user", "content": "hi"}]))

        self.assertEqual(captured.exception.code, "generation_failed")

    def test_sse_status_is_explicit_and_json_encoded(self):
        encoded = encode_sse({
            "status": "failed",
            "code": "llm_timeout",
            "message": PUBLIC_FAILURE_MESSAGES["llm_timeout"],
            "done": True,
        }, event="done")

        self.assertTrue(encoded.startswith("event: done\n"))
        payload = json.loads(encoded.split("data: ", 1)[1].strip())
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["code"], "llm_timeout")
        self.assertTrue(payload["done"])

    def test_disconnected_state_has_public_message(self):
        self.assertEqual(
            PUBLIC_FAILURE_MESSAGES["stream_disconnected"],
            "连接已断开，生成已终止。",
        )


if __name__ == "__main__":
    unittest.main()
