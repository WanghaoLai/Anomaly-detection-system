"""稳定领域模型、端口与访问控制。

旧的 ``services.rag.contracts``/``access`` 路径继续可用；新代码只从本层导入。
"""

from .access import AccessPrincipal, DocumentAccessPolicy, KnowledgeAccessPolicy
from .contracts import (
    AuditRecorder,
    Document,
    DocumentLoader,
    DocumentPreprocessor,
    EmbeddingModel,
    IndexWriteResult,
    Node,
    NodeParser,
    ResponseGenerator,
    Reranker,
    RetrievalResult,
    Retriever,
    SourceInfo,
    VectorIndexWriter,
    VectorStore,
)

__all__ = [
    "AccessPrincipal",
    "AuditRecorder",
    "Document",
    "DocumentAccessPolicy",
    "DocumentLoader",
    "DocumentPreprocessor",
    "EmbeddingModel",
    "IndexWriteResult",
    "KnowledgeAccessPolicy",
    "Node",
    "NodeParser",
    "ResponseGenerator",
    "Reranker",
    "RetrievalResult",
    "Retriever",
    "SourceInfo",
    "VectorIndexWriter",
    "VectorStore",
]
