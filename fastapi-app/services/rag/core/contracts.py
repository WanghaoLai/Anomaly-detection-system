"""RAG 领域模型与应用端口。

这是 P0 冻结的应用层边界。模块只能依赖 Python 标准库，公开签名不得包含
DashScope、Chroma、MarkItDown 或 LlamaIndex 的厂商/框架类型。具体实现由适配器
完成，旧调用方继续从本模块导入以保持兼容。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Mapping, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class SourceInfo:
    """原始来源的稳定描述。

    ``storage_key`` 是存储适配器内的相对定位符，不暴露绝对路径；
    历史数据无原文件时允许为 ``None``，并由对账报告显式标记。
    """

    filename: str
    extension: str
    media_type: str
    byte_size: int
    sha256: str
    storage_key: str | None
    uploaded_at: str


@dataclass(frozen=True)
class Document:
    """加载、标准化后的单份文档，等价于后续索引框架的 Document。"""

    text: str
    metadata: Mapping[str, object] = field(default_factory=dict)
    document_id: str | None = None
    source: SourceInfo | None = None


@dataclass(frozen=True)
class Node:
    """可独立向量化和检索的最小语义单元。"""

    text: str
    metadata: Mapping[str, object] = field(default_factory=dict)
    node_id: str | None = None


@dataclass(frozen=True)
class RetrievalResult:
    """统一检索结果；score 始终是越大越相关。"""

    node: Node
    score: float | None = None
    distance: float | None = None


@dataclass(frozen=True)
class IndexWriteResult:
    """向量索引写入的框架无关结果。"""

    collection_name: str
    node_ids: tuple[str, ...]
    dimension: int
    input_node_count: int
    written_node_count: int
    duplicate_node_count: int
    embedding_batches: int
    write_batches: int
    asynchronous: bool
    writer_schema_version: str


@runtime_checkable
class DocumentLoader(Protocol):
    def load(self, file_bytes: bytes, filename: str) -> Document: ...


@runtime_checkable
class DocumentPreprocessor(Protocol):
    def process(self, document: Document) -> tuple[Document, dict]: ...


@runtime_checkable
class NodeParser(Protocol):
    def parse(self, document: Document) -> Sequence[Node]: ...


@runtime_checkable
class EmbeddingModel(Protocol):
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, query: str) -> list[float]: ...

    def embed_queries(self, queries: Sequence[str]) -> list[list[float]]: ...


@runtime_checkable
class VectorStore(Protocol):
    def add(
        self,
        *,
        ids: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        documents: Sequence[str],
        metadatas: Sequence[Mapping[str, object]],
    ) -> None: ...

    def query(self, query_embedding: Sequence[float], top_k: int) -> list[dict]: ...

    def list_nodes(self) -> list[dict]: ...


@runtime_checkable
class VectorIndexWriter(Protocol):
    """蓝绿索引写入端口；应用层不感知 LlamaIndex/Chroma。"""

    def build(
        self,
        *,
        collection_name: str,
        collection_metadata: Mapping[str, object],
        nodes: Sequence[Node],
        expected_dimension: int | None,
    ) -> IndexWriteResult: ...

    async def abuild(
        self,
        *,
        collection_name: str,
        collection_metadata: Mapping[str, object],
        nodes: Sequence[Node],
        expected_dimension: int | None,
    ) -> IndexWriteResult: ...

    def discard(self, collection_name: str) -> bool: ...


@runtime_checkable
class Retriever(Protocol):
    def retrieve(self, query: str, top_k: int) -> list[dict]: ...


@runtime_checkable
class Reranker(Protocol):
    async def rerank(
        self,
        query: str,
        candidates: Sequence[dict],
        *,
        top_k: int,
    ) -> tuple[list[dict], dict]: ...


@runtime_checkable
class AuditRecorder(Protocol):
    async def record(self, payload: dict) -> str | None: ...

    async def update(self, trace_id: str | None, **values) -> bool: ...


@runtime_checkable
class ResponseGenerator(Protocol):
    async def chat(self, messages: list, system_prompt: str | None = None) -> str: ...

    def chat_stream(
        self,
        messages: list,
        system_prompt: str | None = None,
    ) -> AsyncIterator[str]: ...


__all__ = [
    "Document",
    "AuditRecorder",
    "DocumentLoader",
    "DocumentPreprocessor",
    "EmbeddingModel",
    "IndexWriteResult",
    "Node",
    "NodeParser",
    "ResponseGenerator",
    "Reranker",
    "RetrievalResult",
    "Retriever",
    "SourceInfo",
    "VectorStore",
    "VectorIndexWriter",
]
