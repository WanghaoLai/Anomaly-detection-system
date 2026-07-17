import re
from .llm_service import LLMService
from .knowledge_service import KnowledgeService


class ChatService:
    def __init__(self, llm_service: LLMService, knowledge_service: KnowledgeService = None):
        self.llm = llm_service
        self.knowledge = knowledge_service or KnowledgeService()
        self.system_prompt = """你是工业异常检测系统的智能问答助手。

你的职责：
1. 回答用户关于工业异常检测、数据集、算法、知识库资料的问题
2. 帮助用户理解系统中已有的异常检测知识和文档内容
3. 在参考信息不足时，清楚说明不确定之处，并给出可继续补充的信息方向

回答要求：
- 友好、专业、简洁
- 当消息中包含"参考信息"时，优先基于参考信息中的知识库内容回答
- 如果不确定，诚实告知用户
- 适当使用 emoji 增加亲和力"""

    async def process_message(self, user_message: str, history: list, user_id: int = None) -> str:
        """处理用户消息（非流式）"""
        context = self._get_rag_context(user_message)
        messages = self._build_messages(history, user_message, context)
        response = await self.llm.chat(messages, self.system_prompt)
        return await self._handle_tool_calls(response, messages, user_id)

    async def process_message_stream(self, user_message: str, history: list, user_id: int = None):
        """处理用户消息（流式），注入 RAG 上下文"""
        rag_context = self._get_rag_context(user_message)
        messages = self._build_messages(history, user_message, rag_context)

        async for chunk in self.llm.chat_stream(messages, self.system_prompt):
            yield chunk

    def _get_rag_context(self, query: str) -> str:
        """从 ChromaDB 知识库检索相关上下文"""
        try:
            results = self.knowledge.search(query, top_k=3)
            if not results:
                return ""

            doc_parts = []
            for r in results:
                doc_parts.append(f"- {r['content']}")

            context = ""
            if doc_parts:
                context += "相关知识库信息：\n" + "\n".join(doc_parts) + "\n\n"
            return context.strip()
        except Exception:
            pass
        return ""

    def _parse_tool_calls(self, text: str) -> list:
        """解析 [TOOL_CALL:name:params] 格式"""
        pattern = r"\[TOOL_CALL:([^:\[\]]+):([^\[\]]*)\]"
        matches = re.findall(pattern, text)
        return [(name.strip(), params.strip()) for name, params in matches]

    async def _execute_tool(self, tool_name: str, params_str: str, user_id: int = None) -> str:
        """执行工具调用"""
        return f"当前系统未启用工具调用：{tool_name}"

    async def _handle_tool_calls(self, response: str, messages: list, user_id: int = None) -> str:
        """处理工具调用并返回最终回答（非流式版本），RAG + 工具结果合并"""
        tool_calls = self._parse_tool_calls(response)
        if not tool_calls:
            return response

        tool_results = []
        for tool_name, params_str in tool_calls:
            result = await self._execute_tool(tool_name, params_str, user_id)
            tool_results.append(result)

        messages.append({"role": "assistant", "content": "正在查询数据..."})
        # RAG 上下文已在上游 _build_messages 时注入第一条 user message，
        # 这里提取最后一条 user message 中的 context 用于合并提示
        rag_context = self._extract_context_from_messages(messages)
        messages.append({"role": "user", "content": self._format_tool_response(tool_results, rag_context)})

        return await self.llm.chat(messages, self.system_prompt)

    def _extract_context_from_messages(self, messages: list) -> str:
        """从消息列表中提取 RAG 上下文（位于最后一条 user message 的 '参考信息：' 之后）"""
        for msg in reversed(messages):
            if msg.get("role") == "user" and "参考信息：" in msg.get("content", ""):
                parts = msg["content"].split("参考信息：", 1)
                return parts[1].strip() if len(parts) > 1 else ""
        return ""

    def _format_tool_response(self, tool_results: list, rag_context: str = "") -> str:
        """格式化工具执行结果，与 RAG 上下文合并引导 LLM 综合回答"""
        msg = f"工具执行结果：\n{chr(10).join(tool_results)}\n\n"
        if rag_context:
            msg += (
                "另外，以下是之前检索到的参考信息，可能对回答有帮助：\n"
                f"{rag_context}\n\n"
            )
        msg += "请综合以上工具查询结果和参考信息，完整、准确地回答用户的问题。"
        return msg

    def _build_messages(self, history: list, user_message: str, context: str = "") -> list:
        """构建消息列表"""
        messages = []

        for msg in history[-6:]:
            messages.append({"role": msg['role'], "content": msg['content']})

        if context:
            user_content = f"{user_message}\n\n---\n参考信息：\n{context}"
        else:
            user_content = user_message

        messages.append({"role": "user", "content": user_content})
        return messages
