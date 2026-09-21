import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from httpx import ASGITransport, AsyncClient


BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from api import chat as chat_api  # noqa: E402
from common.auth import get_current_admin  # noqa: E402
from common.chat_history import conversation_page, message_page, recent_messages  # noqa: E402
from main import app  # noqa: E402
from models import Admin, AdminConversation, Conversation, Message, User  # noqa: E402
from tortoise import Tortoise, connections  # noqa: E402


class ChatHistoryPaginationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(db_url="sqlite://:memory:", modules={"models": ["models"]})
        self.addAsyncCleanup(self._shutdown_db)
        await Tortoise.generate_schemas()
        self.user = await User.create(username="history-user", password="x", role="用户")
        self.conversation = await Conversation.create(user_id=self.user.id, title="history")
        for index in range(1, 13):
            await Message.create(
                conversation_id=self.conversation.id,
                role="user" if index % 2 else "assistant",
                content=f"message-{index}",
            )

    @staticmethod
    async def _shutdown_db():
        await connections.close_all(discard=True)
        await Tortoise._reset_apps()

    async def test_cursor_pages_are_chronological_without_overlap(self):
        newest = await message_page(
            Message,
            conversation_id=self.conversation.id,
            before_id=None,
            page_size=5,
        )
        older = await message_page(
            Message,
            conversation_id=self.conversation.id,
            before_id=newest["nextBeforeId"],
            page_size=5,
        )

        self.assertEqual([item.content for item in newest["items"]], [
            "message-8", "message-9", "message-10", "message-11", "message-12"
        ])
        self.assertEqual([item.content for item in older["items"]], [
            "message-3", "message-4", "message-5", "message-6", "message-7"
        ])
        self.assertTrue(newest["hasMore"])
        self.assertTrue(older["hasMore"])

    async def test_recent_context_is_bounded_and_chronological(self):
        rows = await recent_messages(
            Message,
            conversation_id=self.conversation.id,
            history_limit=4,
        )
        self.assertEqual(len(rows), 5)
        self.assertEqual(
            [item.content for item in rows],
            ["message-8", "message-9", "message-10", "message-11", "message-12"],
        )

    async def test_conversation_cursor_pages_tied_timestamps_without_overlap(self):
        fixed = datetime(2025, 1, 1, tzinfo=UTC)
        ids = [self.conversation.id]
        for index in range(4):
            item = await Conversation.create(user_id=self.user.id, title=f"chat-{index}")
            ids.append(item.id)
        await Conversation.filter(id__in=ids).update(updated_at=fixed)

        first = await conversation_page(
            Conversation.filter(user_id=self.user.id),
            before_at=None, before_id=None, page_size=2,
        )
        second = await conversation_page(
            Conversation.filter(user_id=self.user.id),
            before_at=datetime.fromisoformat(first["nextBeforeAt"]),
            before_id=first["nextBeforeId"], page_size=2,
        )
        third = await conversation_page(
            Conversation.filter(user_id=self.user.id),
            before_at=datetime.fromisoformat(second["nextBeforeAt"]),
            before_id=second["nextBeforeId"], page_size=2,
        )
        self.assertEqual(
            [item.id for page in (first, second, third) for item in page["items"]],
            list(reversed(ids)),
        )
        self.assertFalse(third["hasMore"])

    async def test_sending_message_moves_conversation_to_recent_activity(self):
        old = datetime.now(UTC) - timedelta(days=2)
        await Conversation.filter(id=self.conversation.id).update(updated_at=old)
        newer = await Conversation.create(user_id=self.user.id, title="newer")

        async def events(*args, **kwargs):
            yield {"type": "content", "content": "answer"}

        request = SimpleNamespace(is_disconnected=mock.AsyncMock(return_value=False))
        with mock.patch.object(chat_api.chat_service, "process_message_events", side_effect=events):
            response = await chat_api.send_message(
                chat_api.MessageRequest(
                    conversation_id=self.conversation.id, message="question"
                ),
                request,
                current_user={"user_id": self.user.id, "role": "用户"},
            )
            async for _ in response.body_iterator:
                pass
        page = await conversation_page(
            Conversation.filter(user_id=self.user.id),
            before_at=None, before_id=None, page_size=2,
        )
        self.assertEqual(page["items"][0].id, self.conversation.id)
        self.assertEqual(page["items"][1].id, newer.id)

    async def test_user_and_admin_list_endpoints_return_bounded_pages(self):
        admin = await Admin.create(username="chat-admin", password="x", role="管理员")
        for index in range(3):
            await Conversation.create(user_id=self.user.id, title=f"user-{index}")
            await AdminConversation.create(admin_id=admin.id, title=f"admin-{index}")
        app.dependency_overrides[chat_api.get_current_chat_user] = lambda: {
            "user_id": self.user.id, "role": "用户"
        }
        app.dependency_overrides[get_current_admin] = lambda: {
            "user_id": admin.id, "role": "管理员"
        }
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                for path in ("/api/chat/conversations", "/api/admin/chat/conversations"):
                    legacy = await client.get(path)
                    self.assertEqual(legacy.status_code, 200)
                    self.assertIsInstance(legacy.json()["data"], list)
                    first = await client.get(path, params={"pageSize": 2})
                    self.assertEqual(first.status_code, 200)
                    body = first.json()["data"]
                    self.assertEqual(len(body["items"]), 2)
                    self.assertTrue(body["hasMore"])
                    second = await client.get(path, params={
                        "pageSize": 2,
                        "beforeAt": body["nextBeforeAt"],
                        "beforeId": body["nextBeforeId"],
                    })
                    self.assertEqual(second.status_code, 200)
                    expected = 2 if path == "/api/chat/conversations" else 1
                    self.assertEqual(len(second.json()["data"]["items"]), expected)
        finally:
            app.dependency_overrides.clear()


if __name__ == "__main__":
    unittest.main()
