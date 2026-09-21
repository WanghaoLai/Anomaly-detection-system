import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from common.chat_history import message_page, recent_messages  # noqa: E402
from models import Conversation, Message, User  # noqa: E402
from tortoise import Tortoise, connections  # noqa: E402


class ChatHistoryPaginationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(db_url="sqlite://:memory:", modules={"models": ["models"]})
        self.addAsyncCleanup(self._shutdown_db)
        await Tortoise.generate_schemas()
        user = await User.create(username="history-user", password="x", role="用户")
        self.conversation = await Conversation.create(user_id=user.id, title="history")
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


if __name__ == "__main__":
    unittest.main()
