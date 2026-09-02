"""RAG 模块分层声明与自动化依赖守卫。"""

from __future__ import annotations


DOMAIN_MODULES = (
    "services.rag.core.contracts",
    "services.rag.core.access",
)

# 纯业务编排/算法：不得直接导入文档、模型或向量数据库厂商 SDK。
APPLICATION_MODULES = (
    "services.rag.document.markdown",
    "services.rag.document.splitting",
    "services.rag.document.pipeline",
    "services.rag.search.retrieval",
    "services.rag.search.lexical",
    "services.rag.search.pipeline",
    "services.rag.answering.context",
    "services.rag.answering.prompting",
    "services.rag.answering.grounding",
    "services.rag.answering.llm_types",
    "services.rag.operations.sse",
)

# 基础设施适配器：把外部能力转换为 contracts.py 中的稳定端口。
ADAPTER_MODULES = (
    "services.rag.document.loading",
    "services.rag.document.parsing",
    "services.rag.document.storage",
    "services.rag.indexing.embedding",
    "services.rag.indexing.vector_store",
    "services.rag.indexing.writer",
    "services.rag.indexing.qdrant_store",
    "services.rag.indexing.qdrant_writer",
    "services.rag.search.reranking",
)

OPERATIONS_MODULES = (
    "services.rag.operations.audit",
    "services.rag.operations.sse",
)

COMPATIBILITY_MODULES = (
    "services.rag.contracts",
    "services.rag.access",
    "services.rag.loaders",
    "services.rag.splitters",
    "services.rag.llamaindex_parser",
    "services.rag.ingestion",
    "services.rag.artifacts",
    "services.rag.embeddings",
    "services.rag.vector_store",
    "services.rag.llamaindex_indexing",
    "services.rag.retrieval",
    "services.rag.lexical",
    "services.rag.reranking",
    "services.rag.context",
    "services.rag.generation",
    "services.rag.grounding",
    "services.rag.audit",
    "services.rag.sse",
)

# API 只依赖这些应用门面；旧平铺模块作为兼容路径保留。
PUBLIC_FACADES = (
    "services.knowledge_service.KnowledgeService",
    "services.chat_service.ChatService",
)

FORBIDDEN_APPLICATION_IMPORTS = frozenset({
    "chromadb",
    "dashscope",
    "llama_index",
    "markitdown",
    "qdrant_client",
})


__all__ = [
    "ADAPTER_MODULES",
    "APPLICATION_MODULES",
    "COMPATIBILITY_MODULES",
    "DOMAIN_MODULES",
    "FORBIDDEN_APPLICATION_IMPORTS",
    "OPERATIONS_MODULES",
    "PUBLIC_FACADES",
]
