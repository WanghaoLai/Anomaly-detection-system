"""Vector-database-neutral persistent document embedding cache."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import sqlite3
import threading
import time
import zlib
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


logger = logging.getLogger(__name__)
CACHE_SCHEMA_VERSION = "embedding-cache-v1"


@dataclass(frozen=True)
class EmbeddingCacheIdentity:
    provider: str
    model: str
    schema_version: str
    dimension: int
    normalized: bool
    text_type: str
    node_id: str
    text_sha256: str

    @property
    def key(self) -> str:
        payload = {
            "cache_schema_version": CACHE_SCHEMA_VERSION,
            "provider": self.provider,
            "model": self.model,
            "embedding_schema_version": self.schema_version,
            "dimension": self.dimension,
            "normalized": self.normalized,
            "text_type": self.text_type,
            "node_id": self.node_id,
            "text_sha256": self.text_sha256,
        }
        return hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EmbeddingBuildStats:
    cache_hits: int = 0
    cache_misses: int = 0
    generated_embeddings: int = 0
    embedding_batches: int = 0
    embedding_api_calls: int = 0
    embedding_retry_count: int = 0
    cache_invalid_entries: int = 0
    cache_read_failures: int = 0
    cache_write_failures: int = 0
    embedding_seconds: float = 0.0


class SQLiteEmbeddingCache:
    """SQLite is a local derivative store; failures must never block indexing."""

    def __init__(self, path: str | Path, *, enabled: bool = True) -> None:
        self.path = Path(path)
        self.enabled = bool(enabled)
        self._lock = threading.RLock()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=10.0)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS document_embeddings (
                cache_key TEXT PRIMARY KEY,
                cache_schema_version TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                embedding_schema_version TEXT NOT NULL,
                dimension INTEGER NOT NULL,
                normalized INTEGER NOT NULL,
                text_type TEXT NOT NULL,
                node_id TEXT NOT NULL,
                text_sha256 TEXT NOT NULL,
                vector_blob BLOB NOT NULL,
                created_at REAL NOT NULL,
                accessed_at REAL NOT NULL
            )
        """)
        return connection

    @staticmethod
    def _encode(vector: Sequence[float]) -> bytes:
        payload = json.dumps(
            [float(value) for value in vector], separators=(",", ":")
        ).encode("utf-8")
        return zlib.compress(payload, level=6)

    @staticmethod
    def _decode(
        blob: bytes, dimension: int, *, normalized: bool
    ) -> list[float]:
        value = json.loads(zlib.decompress(bytes(blob)).decode("utf-8"))
        if not isinstance(value, list) or len(value) != dimension:
            raise ValueError("缓存向量维度不一致")
        vector = [float(item) for item in value]
        if not vector or not all(math.isfinite(item) for item in vector):
            raise ValueError("缓存向量为空或包含 NaN/Infinity")
        norm = math.sqrt(sum(item * item for item in vector))
        if norm <= 0:
            raise ValueError("缓存向量为零范数")
        if normalized and abs(norm - 1.0) > 1e-3:
            raise ValueError("缓存向量不满足归一化契约")
        return vector

    def get_many(
        self, identities: Sequence[EmbeddingCacheIdentity]
    ) -> tuple[dict[str, list[float]], int, int]:
        if not self.enabled or not identities:
            return {}, 0, 0
        valid: dict[str, list[float]] = {}
        invalid = 0
        try:
            with self._lock, closing(self._connect()) as connection:
                now = time.time()
                for identity in identities:
                    row = connection.execute(
                        "SELECT vector_blob FROM document_embeddings "
                        "WHERE cache_key = ?", (identity.key,),
                    ).fetchone()
                    if row is None:
                        continue
                    try:
                        vector = self._decode(
                            row[0], identity.dimension,
                            normalized=identity.normalized,
                        )
                    except Exception:
                        invalid += 1
                        continue
                    valid[identity.node_id] = vector
                    connection.execute(
                        "UPDATE document_embeddings SET accessed_at = ? "
                        "WHERE cache_key = ?", (now, identity.key),
                    )
                connection.commit()
            return valid, invalid, 0
        except Exception:
            logger.warning("Embedding Cache 读取失败，已降级为重新生成", exc_info=True)
            return {}, invalid, 1

    def put_many(
        self,
        entries: Sequence[tuple[EmbeddingCacheIdentity, Sequence[float]]],
    ) -> int:
        if not self.enabled or not entries:
            return 0
        try:
            with self._lock, closing(self._connect()) as connection:
                now = time.time()
                connection.executemany("""
                    INSERT INTO document_embeddings (
                        cache_key, cache_schema_version, provider, model,
                        embedding_schema_version, dimension, normalized,
                        text_type, node_id, text_sha256, vector_blob,
                        created_at, accessed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        vector_blob=excluded.vector_blob,
                        accessed_at=excluded.accessed_at
                """, [
                    (
                        identity.key, CACHE_SCHEMA_VERSION, identity.provider,
                        identity.model, identity.schema_version,
                        identity.dimension, int(identity.normalized),
                        identity.text_type, identity.node_id,
                        identity.text_sha256, self._encode(vector), now, now,
                    )
                    for identity, vector in entries
                ])
                connection.commit()
            return 0
        except Exception:
            logger.warning("Embedding Cache 写入失败，索引继续使用新向量", exc_info=True)
            return 1


class CachedNodeEmbedder:
    """Embed stable nodes independently of the destination vector database."""

    def __init__(
        self,
        *,
        embedding_adapter,
        cache: SQLiteEmbeddingCache,
        provider: str,
        model: str,
        schema_version: str,
        normalized: bool,
    ) -> None:
        self.embedding_adapter = embedding_adapter
        self.cache = cache
        self.provider = str(provider)
        self.model = str(model)
        self.schema_version = str(schema_version)
        self.normalized = bool(normalized)

    @staticmethod
    def _text_sha256(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _identities(
        self, nodes, dimension: int
    ) -> list[EmbeddingCacheIdentity]:
        return [EmbeddingCacheIdentity(
            provider=self.provider,
            model=self.model,
            schema_version=self.schema_version,
            dimension=int(dimension),
            normalized=self.normalized,
            text_type="document",
            node_id=str(node.node_id),
            text_sha256=self._text_sha256(str(node.text)),
        ) for node in nodes]

    def _backend_metrics(self) -> Mapping[str, int]:
        method = getattr(self.embedding_adapter, "backend_metrics_snapshot", None)
        return dict(method() if method is not None else {})

    @staticmethod
    def _metric_delta(
        after: Mapping[str, int], before: Mapping[str, int], key: str
    ) -> int:
        return max(0, int(after.get(key, 0)) - int(before.get(key, 0)))

    @staticmethod
    def _validate_generated(
        vectors: Sequence[Sequence[float]],
        expected_count: int,
        expected_dimension: int | None,
        normalized: bool,
    ) -> list[list[float]]:
        if len(vectors) != expected_count:
            raise RuntimeError(
                "Embedding 数量与 Cache Miss 数量不一致: "
                f"expected={expected_count}, actual={len(vectors)}"
            )
        clean: list[list[float]] = []
        dimensions: set[int] = set()
        for raw in vectors:
            values = [float(value) for value in raw]
            if not values or not all(math.isfinite(value) for value in values):
                raise RuntimeError("Embedding 为空或包含 NaN/Infinity")
            norm = math.sqrt(sum(value * value for value in values))
            if norm <= 0:
                raise RuntimeError("Embedding 为零范数向量")
            if normalized and abs(norm - 1.0) > 1e-3:
                raise RuntimeError("Embedding 不满足归一化契约")
            dimensions.add(len(values))
            clean.append(values)
        if len(dimensions) > 1:
            raise RuntimeError(f"Embedding 维度不统一: {sorted(dimensions)}")
        if (
            dimensions and expected_dimension is not None
            and next(iter(dimensions)) != int(expected_dimension)
        ):
            raise RuntimeError(
                "Embedding 维度与当前发布版本不一致: "
                f"published={expected_dimension}, actual={next(iter(dimensions))}"
            )
        return clean

    def embed(
        self, nodes, *, expected_dimension: int | None
    ) -> tuple[dict[str, list[float]], EmbeddingBuildStats]:
        started = time.perf_counter()
        identities = (
            self._identities(nodes, expected_dimension)
            if expected_dimension is not None else []
        )
        cached, invalid, read_failures = self.cache.get_many(identities)
        missing = [node for node in nodes if str(node.node_id) not in cached]
        before = self._backend_metrics()
        raw_generated = (
            self.embedding_adapter.get_text_embedding_batch([
                str(node.text) for node in missing
            ]) if missing else []
        )
        generated = self._validate_generated(
            raw_generated, len(missing), expected_dimension, self.normalized
        )
        after = self._backend_metrics()
        vectors = dict(cached)
        for node, vector in zip(missing, generated):
            vectors[str(node.node_id)] = list(vector)
        actual_dimension = len(generated[0]) if generated else expected_dimension
        write_failures = 0
        if missing and actual_dimension is not None:
            store_identities = self._identities(missing, int(actual_dimension))
            write_failures = self.cache.put_many(list(zip(
                store_identities, generated
            )))
        batch_size = max(1, int(self.embedding_adapter.embed_batch_size))
        computed_batches = math.ceil(len(missing) / batch_size) if missing else 0
        return vectors, EmbeddingBuildStats(
            cache_hits=len(cached),
            cache_misses=len(missing),
            generated_embeddings=len(generated),
            embedding_batches=computed_batches,
            embedding_api_calls=(
                self._metric_delta(after, before, "api_calls")
                or computed_batches
            ),
            embedding_retry_count=self._metric_delta(
                after, before, "retries"
            ),
            cache_invalid_entries=invalid,
            cache_read_failures=read_failures,
            cache_write_failures=write_failures,
            embedding_seconds=round(time.perf_counter() - started, 6),
        )

    async def aembed(
        self, nodes, *, expected_dimension: int | None
    ) -> tuple[dict[str, list[float]], EmbeddingBuildStats]:
        return await asyncio.to_thread(
            self.embed, nodes, expected_dimension=expected_dimension
        )


__all__ = [
    "CACHE_SCHEMA_VERSION",
    "CachedNodeEmbedder",
    "EmbeddingBuildStats",
    "EmbeddingCacheIdentity",
    "SQLiteEmbeddingCache",
]
