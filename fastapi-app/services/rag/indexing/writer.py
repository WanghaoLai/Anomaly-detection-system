"""LlamaIndex Embedding + VectorStoreIndex 蓝绿写入适配器。"""

from __future__ import annotations

import asyncio
import json
import math
import time
from importlib.metadata import version
from typing import Mapping, Sequence

from llama_index.core import StorageContext, VectorStoreIndex
from llama_index.core.indices.utils import async_embed_nodes, embed_nodes
from llama_index.core.schema import NodeRelationship, RelatedNodeInfo, TextNode
# llama-index 的 Chroma 集成在模块顶层 import chromadb（连带 onnxruntime）。
# 导入移入 _write_preembedded：QdrantIndexWriter 完整覆盖该方法，
# Qdrant 模式下 chromadb 不再被加载。

from ..core.contracts import IndexWriteResult, Node
from .cache import EmbeddingBuildStats


INDEX_WRITER_SCHEMA_VERSION = "llamaindex-blue-green-index-v1"


def _canonical_metadata(metadata: Mapping[str, object]) -> str:
    return json.dumps(
        dict(metadata), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str,
    )


class LlamaIndexChromaIndexWriter:
    """用 LlamaIndex 生成 embedding 并把 TextNode 写入 Chroma。

    Chroma SDK 只存在本适配器中。替换向量库时实现同一
    ``VectorIndexWriter`` 端口即可，不需要改变 KnowledgeService 的索引算法。
    """

    provider = "chroma"

    def __init__(
        self,
        *,
        client_provider,
        embedding_adapter,
        node_embedder=None,
        insert_batch_size: int = 100,
    ) -> None:
        if insert_batch_size <= 0:
            raise ValueError("insert_batch_size 必须大于 0")
        self._client_provider = client_provider
        self.embedding_adapter = embedding_adapter
        self.node_embedder = node_embedder
        self.insert_batch_size = int(insert_batch_size)

    @property
    def client(self):
        return self._client_provider()

    def _collection_names(self) -> set[str]:
        return {item.name for item in self.client.list_collections()}

    @staticmethod
    def _deduplicate(nodes: Sequence[Node]) -> tuple[list[Node], int]:
        unique: dict[str, Node] = {}
        duplicates = 0
        for node in nodes:
            node_id = str(node.node_id or "")
            if not node_id:
                raise ValueError("Node 缺少稳定 node_id")
            existing = unique.get(node_id)
            if existing is None:
                unique[node_id] = node
                continue
            if (
                existing.text != node.text
                or _canonical_metadata(existing.metadata)
                != _canonical_metadata(node.metadata)
            ):
                raise RuntimeError(f"Node ID 冲突且内容不同: {node_id}")
            duplicates += 1
        return [unique[node_id] for node_id in sorted(unique)], duplicates

    @staticmethod
    def _to_llama_nodes(nodes: Sequence[Node]) -> list[TextNode]:
        result = []
        for node in nodes:
            metadata = {
                str(key): value
                for key, value in dict(node.metadata).items()
                if value is not None and isinstance(value, (str, int, float, bool))
            }
            excluded = list(metadata.keys())
            document_id = str(
                metadata.get("doc_id") or metadata.get("document_id") or ""
            )
            relationships = (
                {
                    NodeRelationship.SOURCE: RelatedNodeInfo(
                        node_id=document_id,
                        metadata={"filename": metadata.get("filename", "")},
                    )
                }
                if document_id
                else {}
            )
            result.append(TextNode(
                id_=str(node.node_id),
                text=node.text,
                metadata=metadata,
                relationships=relationships,
                excluded_embed_metadata_keys=excluded,
                excluded_llm_metadata_keys=excluded,
            ))
        return result

    @staticmethod
    def _attach_and_validate_embeddings(
        nodes: list[TextNode],
        embeddings: Mapping[str, Sequence[float]],
        expected_dimension: int | None,
    ) -> tuple[list[TextNode], int]:
        if len(embeddings) != len(nodes):
            raise RuntimeError(
                f"Embedding 数量与 Node 数量不一致: "
                f"nodes={len(nodes)}, embeddings={len(embeddings)}"
            )
        dimensions: set[int] = set()
        embedded_nodes = []
        for node in nodes:
            raw = embeddings.get(node.node_id)
            if raw is None:
                raise RuntimeError(f"Node 缺少 Embedding: {node.node_id}")
            try:
                vector = [float(value) for value in raw]
            except (TypeError, ValueError) as exc:
                raise RuntimeError("Embedding 包含非数值") from exc
            if not vector or not all(math.isfinite(value) for value in vector):
                raise RuntimeError("Embedding 为空或包含 NaN/Infinity")
            if sum(value * value for value in vector) <= 0:
                raise RuntimeError("Embedding 为零范数向量")
            dimensions.add(len(vector))
            embedded = node.model_copy()
            embedded.embedding = vector
            embedded.metadata = {
                **dict(embedded.metadata),
                "embedding_dim": len(vector),
            }
            embedded_nodes.append(embedded)
        if len(dimensions) != 1:
            raise RuntimeError(f"Embedding 维度不统一: {sorted(dimensions)}")
        dimension = next(iter(dimensions))
        if expected_dimension is not None and dimension != int(expected_dimension):
            raise RuntimeError(
                f"Embedding 维度与当前发布版本不一致: "
                f"published={expected_dimension}, candidate={dimension}"
            )
        return embedded_nodes, dimension

    def _write_preembedded(
        self,
        *,
        collection_name: str,
        collection_metadata: Mapping[str, object],
        nodes: list[TextNode],
        dimension: int,
    ) -> None:
        metadata = {
            **dict(collection_metadata),
            "embedding_dimension": int(dimension),
            "index_framework": "llamaindex",
            "index_writer_schema_version": INDEX_WRITER_SCHEMA_VERSION,
            "llama_index_core_version": version("llama-index-core"),
            "llama_index_chroma_version": version(
                "llama-index-vector-stores-chroma"
            ),
        }
        collection = self.client.create_collection(
            name=collection_name,
            metadata=metadata,
        )
        if not nodes:
            return
        from llama_index.vector_stores.chroma import (
            ChromaVectorStore as LlamaChromaVectorStore,
        )

        vector_store = LlamaChromaVectorStore(chroma_collection=collection)
        storage_context = StorageContext.from_defaults(vector_store=vector_store)
        # Node 已经通过 LlamaIndex embed_nodes 附带 embedding，此处
        # VectorStoreIndex 只负责批量编排和向量库写入，不会重复调模型。
        VectorStoreIndex(
            nodes=nodes,
            storage_context=storage_context,
            embed_model=self.embedding_adapter,
            insert_batch_size=self.insert_batch_size,
        )

    def _result(
        self,
        *,
        collection_name: str,
        input_count: int,
        unique_nodes: Sequence[Node],
        duplicates: int,
        dimension: int,
        asynchronous: bool,
        embedding_stats: EmbeddingBuildStats,
        index_write_seconds: float,
        index_validation_seconds: float,
        index_build_seconds: float,
    ) -> IndexWriteResult:
        write_batches = math.ceil(len(unique_nodes) / self.insert_batch_size)
        embedding_batches = embedding_stats.embedding_batches
        return IndexWriteResult(
            collection_name=collection_name,
            node_ids=tuple(str(node.node_id) for node in unique_nodes),
            dimension=dimension,
            input_node_count=input_count,
            written_node_count=len(unique_nodes),
            duplicate_node_count=duplicates,
            embedding_batches=embedding_batches,
            write_batches=write_batches,
            asynchronous=asynchronous,
            writer_schema_version=INDEX_WRITER_SCHEMA_VERSION,
            reused_embedding_count=embedding_stats.cache_hits,
            generated_embedding_count=embedding_stats.generated_embeddings,
            embedding_api_calls=embedding_stats.embedding_api_calls,
            embedding_retry_count=embedding_stats.embedding_retry_count,
            cache_invalid_entries=embedding_stats.cache_invalid_entries,
            cache_read_failures=embedding_stats.cache_read_failures,
            cache_write_failures=embedding_stats.cache_write_failures,
            embedding_seconds=embedding_stats.embedding_seconds,
            index_write_seconds=round(index_write_seconds, 6),
            index_validation_seconds=round(index_validation_seconds, 6),
            index_build_seconds=round(index_build_seconds, 6),
        )

    def _embed_sync(
        self, native_nodes: list[TextNode], expected_dimension: int | None
    ) -> tuple[Mapping[str, Sequence[float]], EmbeddingBuildStats]:
        if self.node_embedder is not None:
            return self.node_embedder.embed(
                native_nodes, expected_dimension=expected_dimension
            )
        started = time.perf_counter()
        vectors = embed_nodes(native_nodes, self.embedding_adapter)
        batches = math.ceil(
            len(native_nodes) / self.embedding_adapter.embed_batch_size
        ) if native_nodes else 0
        return vectors, EmbeddingBuildStats(
            cache_misses=len(native_nodes),
            generated_embeddings=len(native_nodes),
            embedding_batches=batches,
            embedding_api_calls=batches,
            embedding_seconds=round(time.perf_counter() - started, 6),
        )

    async def _embed_async(
        self, native_nodes: list[TextNode], expected_dimension: int | None
    ) -> tuple[Mapping[str, Sequence[float]], EmbeddingBuildStats]:
        if self.node_embedder is not None:
            return await self.node_embedder.aembed(
                native_nodes, expected_dimension=expected_dimension
            )
        started = time.perf_counter()
        vectors = await async_embed_nodes(native_nodes, self.embedding_adapter)
        batches = math.ceil(
            len(native_nodes) / self.embedding_adapter.embed_batch_size
        ) if native_nodes else 0
        return vectors, EmbeddingBuildStats(
            cache_misses=len(native_nodes),
            generated_embeddings=len(native_nodes),
            embedding_batches=batches,
            embedding_api_calls=batches,
            embedding_seconds=round(time.perf_counter() - started, 6),
        )

    def validate_collection(
        self,
        *,
        collection_name: str,
        expected_node_ids: Sequence[str],
        expected_dimension: int,
    ) -> dict[str, int]:
        """Full vector validation implemented by the vector-db adapter."""
        collection = self.client.get_collection(name=collection_name)
        data = collection.get(include=["embeddings"])
        ids = [str(item) for item in (data.get("ids") or [])]
        raw_embeddings = data.get("embeddings")
        embeddings = list(raw_embeddings) if raw_embeddings is not None else []
        if len(ids) != len(set(ids)):
            raise RuntimeError("候选索引存在重复 Node ID")
        if set(ids) != {str(item) for item in expected_node_ids}:
            raise RuntimeError("候选索引 Node 集合不完整")
        if len(embeddings) != len(ids):
            raise RuntimeError("候选索引向量数量与 Node 数量不一致")
        for vector in embeddings:
            values = [float(value) for value in vector]
            if len(values) != int(expected_dimension):
                raise RuntimeError("候选索引实际向量维度不一致")
            if not values or not all(math.isfinite(value) for value in values):
                raise RuntimeError("候选索引向量为空或包含 NaN/Infinity")
            if sum(value * value for value in values) <= 0:
                raise RuntimeError("候选索引存在零范数向量")
        return {"node_count": len(ids), "validated_vectors": len(embeddings)}

    def build(
        self,
        *,
        collection_name: str,
        collection_metadata: Mapping[str, object],
        nodes: Sequence[Node],
        expected_dimension: int | None,
    ) -> IndexWriteResult:
        build_started = time.perf_counter()
        if collection_name in self._collection_names():
            raise RuntimeError(f"候选 collection 已存在: {collection_name}")
        unique_nodes, duplicates = self._deduplicate(nodes)
        native_nodes = self._to_llama_nodes(unique_nodes)
        if native_nodes:
            vectors, embedding_stats = self._embed_sync(
                native_nodes, expected_dimension
            )
            native_nodes, dimension = self._attach_and_validate_embeddings(
                native_nodes, vectors, expected_dimension
            )
        else:
            dimension = int(expected_dimension or 0)
            embedding_stats = EmbeddingBuildStats()
        created = False
        try:
            write_started = time.perf_counter()
            self._write_preembedded(
                collection_name=collection_name,
                collection_metadata=collection_metadata,
                nodes=native_nodes,
                dimension=dimension,
            )
            index_write_seconds = time.perf_counter() - write_started
            created = True
            validation_started = time.perf_counter()
            self.validate_collection(
                collection_name=collection_name,
                expected_node_ids=[str(node.node_id) for node in unique_nodes],
                expected_dimension=dimension,
            )
            index_validation_seconds = time.perf_counter() - validation_started
            return self._result(
                collection_name=collection_name,
                input_count=len(nodes),
                unique_nodes=unique_nodes,
                duplicates=duplicates,
                dimension=dimension,
                asynchronous=False,
                embedding_stats=embedding_stats,
                index_write_seconds=index_write_seconds,
                index_validation_seconds=index_validation_seconds,
                index_build_seconds=time.perf_counter() - build_started,
            )
        except Exception:
            if created or collection_name in self._collection_names():
                self.discard(collection_name)
            raise

    async def abuild(
        self,
        *,
        collection_name: str,
        collection_metadata: Mapping[str, object],
        nodes: Sequence[Node],
        expected_dimension: int | None,
    ) -> IndexWriteResult:
        build_started = time.perf_counter()
        if collection_name in self._collection_names():
            raise RuntimeError(f"候选 collection 已存在: {collection_name}")
        unique_nodes, duplicates = self._deduplicate(nodes)
        native_nodes = self._to_llama_nodes(unique_nodes)
        if native_nodes:
            vectors, embedding_stats = await self._embed_async(
                native_nodes, expected_dimension
            )
            native_nodes, dimension = self._attach_and_validate_embeddings(
                native_nodes, vectors, expected_dimension
            )
        else:
            dimension = int(expected_dimension or 0)
            embedding_stats = EmbeddingBuildStats()
        try:
            write_started = time.perf_counter()
            await asyncio.to_thread(
                self._write_preembedded,
                collection_name=collection_name,
                collection_metadata=collection_metadata,
                nodes=native_nodes,
                dimension=dimension,
            )
            index_write_seconds = time.perf_counter() - write_started
            validation_started = time.perf_counter()
            await asyncio.to_thread(
                self.validate_collection,
                collection_name=collection_name,
                expected_node_ids=[str(node.node_id) for node in unique_nodes],
                expected_dimension=dimension,
            )
            index_validation_seconds = time.perf_counter() - validation_started
            return self._result(
                collection_name=collection_name,
                input_count=len(nodes),
                unique_nodes=unique_nodes,
                duplicates=duplicates,
                dimension=dimension,
                asynchronous=True,
                embedding_stats=embedding_stats,
                index_write_seconds=index_write_seconds,
                index_validation_seconds=index_validation_seconds,
                index_build_seconds=time.perf_counter() - build_started,
            )
        except Exception:
            if collection_name in self._collection_names():
                self.discard(collection_name)
            raise

    def discard(self, collection_name: str) -> bool:
        if not str(collection_name).startswith("knowledge_shadow_"):
            raise ValueError("只允许删除 knowledge_shadow_ 候选 collection")
        if collection_name not in self._collection_names():
            return False
        self.client.delete_collection(name=collection_name)
        return True


__all__ = [
    "INDEX_WRITER_SCHEMA_VERSION",
    "LlamaIndexChromaIndexWriter",
]
