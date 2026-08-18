"""RAG 核心组件。

该包只表达检索增强生成的稳定阶段和接口。外部服务保留兼容门面，具体的
MarkItDown、DashScope 与 Chroma 实现通过适配器接入。
"""

from .core import Document, Node, RetrievalResult
from .document import DocumentIngestionPipeline
from .search import HybridResultSelector, RetrievalPolicy

__all__ = [
    "Document",
    "DocumentIngestionPipeline",
    "HybridResultSelector",
    "Node",
    "RetrievalPolicy",
    "RetrievalResult",
]
