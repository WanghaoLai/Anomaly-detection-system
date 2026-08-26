import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from api.admin_chat import AdminMessageRequest  # noqa: E402
from api.chat import MessageRequest  # noqa: E402


class ChatMessageRequestValidationTests(unittest.TestCase):
    """🟡#15 回归：聊天消息长度与会话 ID 的入口边界。"""

    def test_valid_message_accepted(self):
        request = MessageRequest(conversation_id=1, message="如何启动训练？")
        self.assertEqual(request.message, "如何启动训练？")

    def test_oversize_message_rejected(self):
        with self.assertRaises(ValidationError):
            MessageRequest(conversation_id=1, message="x" * 8001)

    def test_empty_message_rejected(self):
        with self.assertRaises(ValidationError):
            MessageRequest(conversation_id=1, message="")

    def test_non_positive_conversation_id_rejected(self):
        with self.assertRaises(ValidationError):
            MessageRequest(conversation_id=0, message="hi")

    def test_admin_message_request_has_same_bounds(self):
        with self.assertRaises(ValidationError):
            AdminMessageRequest(conversation_id=1, message="x" * 8001)
        with self.assertRaises(ValidationError):
            AdminMessageRequest(conversation_id=-1, message="hi")


if __name__ == "__main__":
    unittest.main()
