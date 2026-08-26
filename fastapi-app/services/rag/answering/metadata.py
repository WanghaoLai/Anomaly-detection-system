"""知识库元数据问答：从发布清单渲染确定性回答，不经 LLM。"""

from __future__ import annotations

from .grounding import VerifiedAnswer


class KnowledgeMetadataAnswerer:
    """把服务端文档清单渲染为可直接发布的回答。

    数据全部来自发布控制面（manifest/检索元数据），不存在模型编造
    计数的环节，因此 faithfulness 固定为 1.0，也不产生 K 引用。
    """

    def __init__(self, max_listed_documents: int = 20):
        if max_listed_documents < 1:
            raise ValueError("max_listed_documents 必须为正数")
        self.max_listed_documents = int(max_listed_documents)

    def answer(self, digest: dict) -> VerifiedAnswer | None:
        """digest 结构不合法时返回 None，由调用方退回检索链路。"""

        if not isinstance(digest, dict):
            return None
        raw_documents = digest.get("documents")
        if not isinstance(raw_documents, list):
            return None

        documents: list[dict] = []
        seen_doc_ids: set[str] = set()
        for item in raw_documents:
            if not isinstance(item, dict):
                continue
            filename = str(item.get("filename") or "").strip()
            if not filename:
                continue
            doc_id = str(item.get("doc_id") or "")
            if doc_id in seen_doc_ids:
                continue
            seen_doc_ids.add(doc_id)
            documents.append({"doc_id": doc_id, "filename": filename})

        # 清单非空却全部条目无效说明数据损坏，应退回检索链路而非误报空库。
        if raw_documents and not documents:
            return None

        documents.sort(key=lambda entry: entry["filename"])
        return VerifiedAnswer(
            mode="knowledge_metadata",
            text=self.render(documents, digest.get("total_chunks")),
            citations=(),
            claims=(),
            refusal=False,
            faithfulness=1.0,
            status="completed",
            sources=tuple(documents),
        )

    def render(self, documents: list[dict], total_chunks=None) -> str:
        count = len(documents)
        if count == 0:
            return "当前知识库还没有收录任何文档 📭 先上传资料后就可以在这里提问啦。"

        chunk_note = (
            f"（共 {int(total_chunks)} 个分块）"
            if isinstance(total_chunks, int) and total_chunks > 0
            else ""
        )
        lines = [f"当前知识库共收录 **{count} 篇文档**{chunk_note}："]
        listed = documents[: self.max_listed_documents]
        for index, entry in enumerate(listed, start=1):
            lines.append(f"{index}. {entry['filename']}")
        omitted = count - len(listed)
        if omitted > 0:
            lines.append(f"……其余 {omitted} 篇就不逐一列出啦。")
        lines.append("想了解某篇的内容，直接问我文档里的方法或细节就好 😊")
        return "\n".join(lines)
