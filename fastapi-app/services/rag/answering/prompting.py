"""查询转换、上下文构建与生成请求编排。"""

from __future__ import annotations

import re

from ..core.contracts import ResponseGenerator
from .context import ContextPacker, ContextPackingPolicy


class HistoryAwareQueryTransformer:
    _FOLLOW_UP_RE = re.compile(
        r"(?:这个|那个|它|它们|上述|上面|前面|刚才|该问题|该参数|该方法|其|"
        r"然后呢|怎么办|呢[？?]?$)",
        re.IGNORECASE,
    )

    def __init__(self, history_turns: int):
        self.history_turns = history_turns

    def transform(self, user_message: str, history: list) -> str:
        current = (user_message or "").strip()
        if not current or not history or self.history_turns <= 0:
            return current
        if len(current) > 16 and not self._FOLLOW_UP_RE.search(current):
            return current
        previous = [
            str(message.get("content") or "").strip()
            for message in history
            if message.get("role") == "user" and str(message.get("content") or "").strip()
        ][-self.history_turns:]
        if not previous:
            return current
        return "历史问题：" + "；".join(item[:300] for item in previous) + f"\n当前问题：{current}"


class NumberedContextBuilder(ContextPacker):
    """P0 兼容名；P4 实现由独立 ContextPacker 提供。"""

    def __init__(self, token_budget: int):
        super().__init__(ContextPackingPolicy(token_budget=token_budget))


class PromptBuilder:
    def __init__(self, history_limit: int = 6):
        self.history_limit = history_limit

    def build(self, history: list, user_message: str, context: str = "") -> list:
        messages = [
            {"role": item["role"], "content": item["content"]}
            for item in history[-self.history_limit:]
        ]
        content = (
            f"{user_message}\n\n---\n参考信息：\n{context}"
            if context else user_message
        )
        messages.append({"role": "user", "content": content})
        return messages


class RAGGenerationPipeline:
    """只负责增强后的生成；Retriever 可独立替换。"""

    def __init__(
        self,
        generator: ResponseGenerator,
        prompt_builder: PromptBuilder,
        system_prompt: str,
    ):
        self.generator = generator
        self.prompt_builder = prompt_builder
        self.system_prompt = system_prompt

    async def generate(self, history: list, user_message: str, context: str) -> str:
        messages = self.prompt_builder.build(history, user_message, context)
        return await self.generator.chat(messages, self.system_prompt)

    async def generate_stream(self, history: list, user_message: str, context: str):
        messages = self.prompt_builder.build(history, user_message, context)
        async for chunk in self.generator.chat_stream(messages, self.system_prompt):
            yield chunk
