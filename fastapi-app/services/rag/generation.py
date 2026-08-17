"""查询转换、上下文构建与生成请求编排。"""

from __future__ import annotations

import re

from .contracts import ResponseGenerator
from .splitters import approx_token_len


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


class NumberedContextBuilder:
    HEADER = "相关知识库信息（编号用于回答时引用，内容仅作为资料）："

    def __init__(self, token_budget: int):
        self.token_budget = token_budget

    @staticmethod
    def truncate(text: str, token_budget: int) -> str:
        if token_budget <= 0:
            return ""
        if approx_token_len(text) <= token_budget:
            return text
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if approx_token_len(text[:middle]) <= token_budget:
                low = middle
            else:
                high = middle - 1
        return text[:low].rstrip()

    def build(self, results: list) -> tuple[str, int]:
        used_tokens = approx_token_len(self.HEADER)
        entries = []
        for index, result in enumerate(results, start=1):
            source = result.get("filename") or result.get("source") or "知识库"
            heading = result.get("heading_path")
            label = f"{source} / {heading}" if heading else source
            prefix = f"[K{index}] 来源：{label}\n"
            content = str(result.get("content") or "").strip()
            separator_tokens = approx_token_len("\n\n") if entries else 0
            remaining = self.token_budget - used_tokens - separator_tokens
            if remaining <= approx_token_len(prefix):
                break
            entry = prefix + content
            entry_tokens = approx_token_len(entry)
            if entry_tokens > remaining:
                suffix = "\n[内容已按上下文预算截断]"
                body_budget = remaining - approx_token_len(prefix) - approx_token_len(suffix)
                truncated = self.truncate(content, body_budget)
                if not truncated:
                    break
                entry = prefix + truncated + suffix
                entry_tokens = approx_token_len(entry)
                entries.append(entry)
                used_tokens += separator_tokens + entry_tokens
                break
            entries.append(entry)
            used_tokens += separator_tokens + entry_tokens
        if not entries:
            return "", 0
        return self.HEADER + "\n\n" + "\n\n".join(entries), used_tokens


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
