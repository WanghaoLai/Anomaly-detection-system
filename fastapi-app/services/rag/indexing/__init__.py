"""Embedding、LlamaIndex 索引写入与向量库适配层。"""

from .embedding import DashScopeEmbeddingModel, LlamaIndexEmbeddingAdapter
from .cache import (
    CACHE_SCHEMA_VERSION,
    CachedNodeEmbedder,
    EmbeddingBuildStats,
    EmbeddingCacheIdentity,
    SQLiteEmbeddingCache,
)
from .writer import (
    INDEX_WRITER_SCHEMA_VERSION,
    LlamaIndexChromaIndexWriter,
)
from .vector_store import ChromaVectorStore

__all__ = [
    "ChromaVectorStore",
    "CACHE_SCHEMA_VERSION",
    "CachedNodeEmbedder",
    "DashScopeEmbeddingModel",
    "EmbeddingBuildStats",
    "EmbeddingCacheIdentity",
    "INDEX_WRITER_SCHEMA_VERSION",
    "LlamaIndexChromaIndexWriter",
    "LlamaIndexEmbeddingAdapter",
    "SQLiteEmbeddingCache",
]
