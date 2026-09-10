"""Qdrant client factory and provider adapters.

The application keeps its stable SHA-256 node identifiers. Qdrant point IDs are
deterministic UUIDv5 values derived from those identifiers; the original ID is
always stored in payload and remains the identity used by manifests, citations,
and audit records.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Mapping, Sequence
from urllib.parse import urlparse

from qdrant_client import QdrantClient, models


POINT_ID_SCHEMA_VERSION = "uuid5-node-id-v1"
QDRANT_PAYLOAD_SCHEMA_VERSION = "rag-qdrant-payload-v1"
QDRANT_POINT_NAMESPACE_PREFIX = "urn:anomaly-detection-system:rag-node:"


def point_id_for_node(node_id: str) -> str:
    """Return the stable Qdrant UUID for an application Node ID."""

    value = str(node_id or "").strip()
    if not value:
        raise ValueError("Node ID 不能为空")
    return str(uuid.uuid5(uuid.NAMESPACE_URL, QDRANT_POINT_NAMESPACE_PREFIX + value))


@dataclass(frozen=True)
class QdrantRuntimeConfig:
    mode: str = "local"
    path: str = ""
    url: str = "http://127.0.0.1:6333"
    api_key: str = ""
    timeout_seconds: float = 10.0
    prefer_grpc: bool = False

    def validate(self) -> "QdrantRuntimeConfig":
        mode = self.mode.strip().lower()
        if mode not in {"local", "server"}:
            raise ValueError("AI_QDRANT_MODE 必须为 local 或 server")
        if self.timeout_seconds <= 0:
            raise ValueError("AI_QDRANT_TIMEOUT_SECONDS 必须大于 0")
        if mode == "local" and not self.path.strip():
            raise ValueError("Qdrant local 模式必须配置持久化路径")
        if mode == "server" and not self.url.strip():
            raise ValueError("Qdrant server 模式必须配置 URL")
        if mode == "server":
            scheme = urlparse(self.url.strip()).scheme.lower()
            if scheme not in {"http", "https"}:
                raise ValueError("Qdrant server URL 必须使用 http 或 https")
        return self


def create_qdrant_client(config: QdrantRuntimeConfig) -> QdrantClient:
    """Create the same client API for local and optional server development."""

    config.validate()
    if config.mode.strip().lower() == "local":
        path = Path(config.path).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return QdrantClient(path=str(path))
    parsed = urlparse(config.url.strip())
    # qdrant-client defaults its ``port`` argument to 6333 even when an HTTPS
    # URL omits the port. Managed Cloud endpoints use standard HTTPS/443 in
    # networks where 6333 is unavailable, so make the URL semantics explicit.
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 6333)
    return QdrantClient(
        url=config.url.rstrip("/"),
        port=port,
        api_key=config.api_key or None,
        timeout=config.timeout_seconds,
        prefer_grpc=bool(config.prefer_grpc),
    )


def _qdrant_filter(where: Mapping[str, object] | None) -> models.Filter | None:
    """Translate the deliberately small provider-neutral equality/$in dialect."""

    if not where:
        return None
    conditions = []
    for key, raw in where.items():
        if isinstance(raw, Mapping):
            unsupported = set(raw) - {"$in"}
            if unsupported:
                raise ValueError(
                    f"Qdrant 过滤不支持操作符: {sorted(unsupported)}"
                )
            values = list(raw.get("$in") or [])
            if not values:
                # A must condition that can never match. Callers normally stop
                # before querying, but retaining this guard prevents fail-open.
                values = ["__no_authorized_value__"]
            if not all(isinstance(value, (str, int)) for value in values):
                raise ValueError("Qdrant $in 仅支持字符串或整数")
            match = models.MatchAny(any=values)
        else:
            if not isinstance(raw, (str, int, bool)):
                raise ValueError("Qdrant 等值过滤仅支持标量")
            match = models.MatchValue(value=raw)
        conditions.append(models.FieldCondition(key=str(key), match=match))
    return models.Filter(must=conditions)


class QdrantVectorStore:
    """Thin Qdrant adapter returning the application's stable result shape."""

    def __init__(
        self,
        client_provider: Callable[[], QdrantClient],
        collection_name_provider: Callable[[], str],
        *,
        scroll_batch_size: int = 256,
    ) -> None:
        if scroll_batch_size <= 0:
            raise ValueError("scroll_batch_size 必须大于 0")
        self._client_provider = client_provider
        self._collection_name_provider = collection_name_provider
        self.scroll_batch_size = int(scroll_batch_size)

    @property
    def client(self) -> QdrantClient:
        return self._client_provider()

    @property
    def collection_name(self) -> str:
        return str(self._collection_name_provider())

    def count(self) -> int:
        return int(
            self.client.count(
                collection_name=self.collection_name, exact=True
            ).count
        )

    def add(
        self,
        *,
        ids: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        documents: Sequence[str],
        metadatas: Sequence[Mapping[str, object]],
    ) -> None:
        lengths = {len(ids), len(embeddings), len(documents), len(metadatas)}
        if len(lengths) != 1:
            raise ValueError("Qdrant 写入的 ID/向量/正文/metadata 数量不一致")
        points = []
        for node_id, vector, content, metadata in zip(
            ids, embeddings, documents, metadatas
        ):
            payload = {
                **dict(metadata),
                "node_id": str(node_id),
                "content": str(content),
                "point_id_schema_version": POINT_ID_SCHEMA_VERSION,
                "payload_schema_version": QDRANT_PAYLOAD_SCHEMA_VERSION,
            }
            points.append(models.PointStruct(
                id=point_id_for_node(str(node_id)),
                vector=[float(value) for value in vector],
                payload=payload,
            ))
        if points:
            self.client.upsert(
                collection_name=self.collection_name,
                points=points,
                wait=True,
            )

    def query(
        self,
        query_embedding: Sequence[float],
        top_k: int,
        *,
        where: Mapping[str, object] | None = None,
    ) -> list[dict]:
        response = self.client.query_points(
            collection_name=self.collection_name,
            query=[float(value) for value in query_embedding],
            query_filter=_qdrant_filter(where),
            limit=int(top_k),
            with_payload=True,
            with_vectors=False,
        )
        results = []
        for point in response.points:
            payload = dict(point.payload or {})
            score = float(point.score)
            results.append({
                "node_id": payload.get("node_id"),
                "content": str(payload.pop("content", "")),
                "metadata": payload,
                "score": score,
                "distance": 1.0 - score,
            })
        return results

    def _scroll(
        self,
        *,
        where: Mapping[str, object] | None = None,
        with_vectors: bool = False,
    ) -> list:
        records = []
        offset = None
        while True:
            page, next_offset = self.client.scroll(
                collection_name=self.collection_name,
                scroll_filter=_qdrant_filter(where),
                limit=self.scroll_batch_size,
                offset=offset,
                with_payload=True,
                with_vectors=with_vectors,
            )
            records.extend(page)
            if next_offset is None:
                return records
            if next_offset == offset:
                raise RuntimeError("Qdrant scroll offset 未前进")
            offset = next_offset

    def list_nodes(self) -> list[dict]:
        nodes = []
        for record in self._scroll():
            payload = dict(record.payload or {})
            nodes.append({
                "node_id": payload.get("node_id"),
                "content": str(payload.pop("content", "")),
                "metadata": payload,
            })
        return nodes


class QdrantCollectionAdapter:
    """Temporary collection facade for the legacy KnowledgeService boundary.

    New retrieval/indexing code should use :class:`QdrantVectorStore` directly.
    This facade keeps cross-store transactions and health checks operational
    while those call sites are migrated away from Chroma-shaped responses.
    """

    def __init__(self, client: QdrantClient, name: str):
        self._client = client
        self.name = str(name)
        self._store = QdrantVectorStore(lambda: client, lambda: self.name)

    @property
    def metadata(self) -> dict:
        info = self._client.get_collection(self.name)
        return dict(info.config.metadata or {})

    def count(self) -> int:
        return self._store.count()

    def add(self, *, ids, embeddings, documents, metadatas) -> None:
        self._store.add(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )

    def query(self, *, query_embeddings, n_results, where=None, include=None) -> dict:
        batches = [
            self._store.query(embedding, int(n_results), where=where)
            for embedding in query_embeddings
        ]
        return {
            "ids": [[item["node_id"] for item in results] for results in batches],
            "documents": [
                [item["content"] for item in results] for results in batches
            ],
            "metadatas": [
                [item["metadata"] for item in results] for results in batches
            ],
            "distances": [
                [item["distance"] for item in results] for results in batches
            ],
        }

    @staticmethod
    def _include_flags(include) -> tuple[bool, bool, bool]:
        requested = set(include or [])
        # Chroma get() without include returns its default document/metadata
        # fields; include=[] intentionally requests IDs only.
        if include is None:
            return True, True, False
        return (
            "documents" in requested,
            "metadatas" in requested,
            "embeddings" in requested,
        )

    def get(self, ids=None, where=None, include=None) -> dict:
        include_documents, include_metadatas, include_vectors = (
            self._include_flags(include)
        )
        if ids is not None:
            records = self._client.retrieve(
                collection_name=self.name,
                ids=[point_id_for_node(str(item)) for item in ids],
                with_payload=True,
                with_vectors=include_vectors,
            )
        else:
            records = self._store._scroll(
                where=where, with_vectors=include_vectors
            )
        result = {
            "ids": [str((record.payload or {}).get("node_id") or "") for record in records]
        }
        if include_documents:
            result["documents"] = [
                str((record.payload or {}).get("content") or "")
                for record in records
            ]
        if include_metadatas:
            result["metadatas"] = [
                {
                    key: value
                    for key, value in dict(record.payload or {}).items()
                    if key != "content"
                }
                for record in records
            ]
        if include_vectors:
            result["embeddings"] = [record.vector for record in records]
        return result

    def peek(self, limit: int = 10) -> dict:
        page, _ = self._client.scroll(
            collection_name=self.name,
            limit=int(limit),
            with_payload=True,
            with_vectors=True,
        )
        return {
            "ids": [str((item.payload or {}).get("node_id") or "") for item in page],
            "documents": [str((item.payload or {}).get("content") or "") for item in page],
            "metadatas": [
                {
                    key: value
                    for key, value in dict(item.payload or {}).items()
                    if key != "content"
                }
                for item in page
            ],
            "embeddings": [item.vector for item in page],
        }

    def delete(self, *, ids) -> None:
        point_ids = [point_id_for_node(str(item)) for item in ids]
        if point_ids:
            self._client.delete(
                collection_name=self.name,
                points_selector=point_ids,
                wait=True,
            )

    def update(self, *, ids, metadatas) -> None:
        if len(ids) != len(metadatas):
            raise ValueError("Qdrant update ID/metadata 数量不一致")
        for node_id, metadata in zip(ids, metadatas):
            self._client.set_payload(
                collection_name=self.name,
                payload=dict(metadata),
                points=[point_id_for_node(str(node_id))],
                wait=True,
            )


class QdrantDatabaseAdapter:
    """Small database facade used by provider-aware release operations."""

    provider = "qdrant"

    def __init__(self, client: QdrantClient):
        self.raw_client = client

    def list_collections(self):
        return [
            SimpleNamespace(name=item.name)
            for item in self.raw_client.get_collections().collections
        ]

    def get_collection(self, name: str):
        if not self.raw_client.collection_exists(str(name)):
            raise RuntimeError(f"Qdrant collection 不存在: {name}")
        return QdrantCollectionAdapter(self.raw_client, str(name))

    def delete_collection(self, name: str) -> bool:
        return bool(self.raw_client.delete_collection(str(name)))


__all__ = [
    "POINT_ID_SCHEMA_VERSION",
    "QDRANT_PAYLOAD_SCHEMA_VERSION",
    "QdrantCollectionAdapter",
    "QdrantDatabaseAdapter",
    "QdrantRuntimeConfig",
    "QdrantVectorStore",
    "create_qdrant_client",
    "point_id_for_node",
]
