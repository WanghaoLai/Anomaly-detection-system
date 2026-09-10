"""LlamaIndex embedding orchestration with direct qdrant-client writes."""

from __future__ import annotations

import math
from dataclasses import replace
from importlib.metadata import version
from typing import Mapping, Sequence

from qdrant_client import models

from ..core.contracts import IndexWriteResult
from .qdrant_store import (
    POINT_ID_SCHEMA_VERSION,
    QDRANT_PAYLOAD_SCHEMA_VERSION,
    QdrantVectorStore,
    point_id_for_node,
)
from .writer import LlamaIndexChromaIndexWriter


QDRANT_WRITER_SCHEMA_VERSION = "qdrant-blue-green-index-v1"
QDRANT_PAYLOAD_INDEX_FIELDS = (
    "node_id",
    "doc_id",
    "visibility",
    "allowed_roles",
    "allowed_user_ids",
    "release_id",
)


class QdrantIndexWriter(LlamaIndexChromaIndexWriter):
    """Reuse LlamaIndex Node/Embedding validation and write via qdrant-client.

    The parent class only supplies provider-neutral deduplication, TextNode
    conversion, embedding-cache orchestration, and build lifecycle. Chroma's
    VectorStoreIndex and collection APIs are never used by this writer.
    """

    provider = "qdrant"

    def __init__(
        self,
        *,
        client_provider,
        embedding_adapter,
        node_embedder=None,
        insert_batch_size: int = 100,
        require_payload_indexes: bool = False,
    ) -> None:
        super().__init__(
            client_provider=client_provider,
            embedding_adapter=embedding_adapter,
            node_embedder=node_embedder,
            insert_batch_size=insert_batch_size,
        )
        self.require_payload_indexes = bool(require_payload_indexes)

    def _collection_names(self) -> set[str]:
        return {item.name for item in self.client.get_collections().collections}

    def _write_preembedded(
        self,
        *,
        collection_name: str,
        collection_metadata: Mapping[str, object],
        nodes: list,
        dimension: int,
    ) -> None:
        if dimension <= 0:
            raise RuntimeError("Qdrant collection 向量维度必须大于 0")
        metadata = {
            **dict(collection_metadata),
            "embedding_dimension": int(dimension),
            "index_framework": "llamaindex",
            "index_writer_schema_version": QDRANT_WRITER_SCHEMA_VERSION,
            "point_id_schema_version": POINT_ID_SCHEMA_VERSION,
            "payload_schema_version": QDRANT_PAYLOAD_SCHEMA_VERSION,
            "qdrant_client_version": version("qdrant-client"),
        }
        self.client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(
                size=int(dimension), distance=models.Distance.COSINE
            ),
            metadata=metadata,
        )
        # Embedded local mode does not use payload indexes and warns when they
        # are created. Keep them for optional Server integration tests only.
        if self.require_payload_indexes:
            for field in QDRANT_PAYLOAD_INDEX_FIELDS:
                self.client.create_payload_index(
                    collection_name=collection_name,
                    field_name=field,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                    wait=True,
                )
        store = QdrantVectorStore(
            lambda: self.client,
            lambda: collection_name,
        )
        for start in range(0, len(nodes), self.insert_batch_size):
            batch = nodes[start:start + self.insert_batch_size]
            store.add(
                ids=[str(node.node_id) for node in batch],
                embeddings=[node.get_embedding() for node in batch],
                documents=[str(node.text) for node in batch],
                metadatas=[dict(node.metadata) for node in batch],
            )

    def _result(self, **kwargs) -> IndexWriteResult:
        result = super()._result(**kwargs)
        return replace(
            result,
            writer_schema_version=QDRANT_WRITER_SCHEMA_VERSION,
            write_batches=math.ceil(
                result.written_node_count / self.insert_batch_size
            ) if result.written_node_count else 0,
        )

    @staticmethod
    def _vector_values(record) -> list[float]:
        vector = record.vector
        if isinstance(vector, Mapping):
            if len(vector) != 1:
                raise RuntimeError("Qdrant 候选索引包含未预期的命名向量")
            vector = next(iter(vector.values()))
        if not isinstance(vector, Sequence):
            raise RuntimeError("Qdrant 候选索引向量格式无效")
        return [float(value) for value in vector]

    def validate_collection(
        self,
        *,
        collection_name: str,
        expected_node_ids: Sequence[str],
        expected_dimension: int,
    ) -> dict[str, int]:
        if collection_name not in self._collection_names():
            raise RuntimeError("Qdrant 候选 collection 不存在")
        info = self.client.get_collection(collection_name)
        vector_config = info.config.params.vectors
        if isinstance(vector_config, Mapping):
            if len(vector_config) != 1:
                raise RuntimeError("Qdrant 候选 collection 向量配置不唯一")
            vector_config = next(iter(vector_config.values()))
        if int(vector_config.size) != int(expected_dimension):
            raise RuntimeError("Qdrant collection 向量维度不一致")
        if vector_config.distance != models.Distance.COSINE:
            raise RuntimeError("Qdrant collection 距离类型不是 Cosine")
        if self.require_payload_indexes:
            missing_indexes = set(QDRANT_PAYLOAD_INDEX_FIELDS) - set(
                info.payload_schema
            )
            if missing_indexes:
                raise RuntimeError(
                    f"Qdrant payload index 缺失: {sorted(missing_indexes)}"
                )

        store = QdrantVectorStore(
            lambda: self.client,
            lambda: collection_name,
        )
        records = store._scroll(with_vectors=True)
        expected = {str(item) for item in expected_node_ids}
        actual = []
        point_ids = set()
        for record in records:
            payload = dict(record.payload or {})
            node_id = str(payload.get("node_id") or "")
            if not node_id:
                raise RuntimeError("Qdrant Point 缺少原始 Node ID")
            if str(record.id) != point_id_for_node(node_id):
                raise RuntimeError("Qdrant Point ID 与 Node ID 确定性映射不一致")
            if payload.get("point_id_schema_version") != POINT_ID_SCHEMA_VERSION:
                raise RuntimeError("Qdrant Point ID schema 不一致")
            if payload.get("payload_schema_version") != QDRANT_PAYLOAD_SCHEMA_VERSION:
                raise RuntimeError("Qdrant payload schema 不一致")
            if not str(payload.get("content") or "").strip():
                raise RuntimeError("Qdrant Point 缺少正文")
            values = self._vector_values(record)
            if len(values) != int(expected_dimension):
                raise RuntimeError("Qdrant Point 向量维度不一致")
            if not all(math.isfinite(value) for value in values):
                raise RuntimeError("Qdrant Point 向量包含 NaN/Infinity")
            if sum(value * value for value in values) <= 0:
                raise RuntimeError("Qdrant Point 存在零范数向量")
            actual.append(node_id)
            point_ids.add(str(record.id))
        if len(actual) != len(set(actual)) or len(point_ids) != len(actual):
            raise RuntimeError("Qdrant 候选索引存在重复 Node/Point ID")
        if set(actual) != expected:
            raise RuntimeError("Qdrant 候选索引 Node 集合不完整")
        exact_count = int(
            self.client.count(collection_name, exact=True).count
        )
        if exact_count != len(actual):
            raise RuntimeError("Qdrant count 与全量 scroll 结果不一致")
        return {
            "node_count": len(actual),
            "validated_vectors": len(actual),
            "validated_point_ids": len(point_ids),
        }

    def discard(self, collection_name: str) -> bool:
        if not str(collection_name).startswith("knowledge_shadow_"):
            raise ValueError("只允许删除 knowledge_shadow_ 候选 collection")
        if collection_name not in self._collection_names():
            return False
        self.client.delete_collection(collection_name)
        return True


__all__ = [
    "QDRANT_PAYLOAD_INDEX_FIELDS",
    "QDRANT_WRITER_SCHEMA_VERSION",
    "QdrantIndexWriter",
]
