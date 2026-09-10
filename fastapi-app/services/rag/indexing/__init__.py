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
from .qdrant_store import (
    POINT_ID_SCHEMA_VERSION,
    QDRANT_PAYLOAD_SCHEMA_VERSION,
    QdrantCollectionAdapter,
    QdrantDatabaseAdapter,
    QdrantRuntimeConfig,
    QdrantVectorStore,
    create_qdrant_client,
    point_id_for_node,
)
from .qdrant_writer import (
    QDRANT_PAYLOAD_INDEX_FIELDS,
    QDRANT_WRITER_SCHEMA_VERSION,
    QdrantIndexWriter,
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
    "POINT_ID_SCHEMA_VERSION",
    "QDRANT_PAYLOAD_INDEX_FIELDS",
    "QDRANT_PAYLOAD_SCHEMA_VERSION",
    "QDRANT_WRITER_SCHEMA_VERSION",
    "QdrantCollectionAdapter",
    "QdrantDatabaseAdapter",
    "QdrantIndexWriter",
    "QdrantRuntimeConfig",
    "QdrantVectorStore",
    "SQLiteEmbeddingCache",
    "create_qdrant_client",
    "point_id_for_node",
]
