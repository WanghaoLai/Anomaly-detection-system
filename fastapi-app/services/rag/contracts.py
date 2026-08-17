"""RAG 各阶段之间的稳定数据契约与可替换接口。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Mapping, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class Document:
    """加载、标准化后的单份文档，等价于后续索引框架的 Document。"""

    text: str
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Node:
    """可独立向量化和检索的最小语义单元。"""

    text: str
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalResult:
    """统一检索结果；score 始终是越大越相关。"""

    node: Node
    score: float | None = None
    distance: float | None = None


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
class Retriever(Protocol):
    def retrieve(self, query: str, top_k: int) -> list[dict]: ...


@runtime_checkable
class ResponseGenerator(Protocol):
    async def chat(self, messages: list, system_prompt: str | None = None) -> str: ...

    def chat_stream(
        self,
        messages: list,
        system_prompt: str | None = None,
    ) -> AsyncIterator[str]: ...
