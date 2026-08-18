"""现有 Chroma collection 的薄适配器。"""

from __future__ import annotations

from typing import Callable, Mapping, Sequence


class ChromaVectorStore:
    """隔离 Chroma 原始响应，使核心检索逻辑不感知 SDK。"""

    def __init__(self, collection_provider: Callable[[], object]):
        self._collection_provider = collection_provider

    @property
    def collection(self):
        return self._collection_provider()

    def add(
        self,
        *,
        ids: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        documents: Sequence[str],
        metadatas: Sequence[Mapping[str, object]],
    ) -> None:
        self.collection.add(
            ids=list(ids),
            embeddings=list(embeddings),
            documents=list(documents),
            metadatas=list(metadatas),
        )

    def query(
        self,
        query_embedding: Sequence[float],
        top_k: int,
        *,
        where: Mapping[str, object] | None = None,
    ) -> list[dict]:
        kwargs = {
            "query_embeddings": [list(query_embedding)],
            "n_results": top_k,
        }
        if where:
            kwargs["where"] = dict(where)
        raw = self.collection.query(**kwargs)
        documents = (raw.get("documents") or [[]])[0]
        metadatas = (raw.get("metadatas") or [[]])[0]
        distances = (raw.get("distances") or [[]])[0]
        raw_ids = raw.get("ids") or []
        ids = raw_ids[0] if raw_ids else [None] * len(documents)
        return [
            {
                "node_id": node_id,
                "content": content,
                "metadata": metadata or {},
                "distance": float(distance),
                "score": 1.0 - float(distance),
            }
            for node_id, content, metadata, distance in zip(
                ids, documents, metadatas, distances
            )
        ]

    def list_nodes(self) -> list[dict]:
        raw = self.collection.get(include=["documents", "metadatas"])
        documents = list(raw.get("documents") or [])
        metadatas = list(raw.get("metadatas") or [])
        ids = list(raw.get("ids") or [])
        if not ids:
            ids = [None] * len(documents)
        return [
            {
                "node_id": node_id,
                "content": content,
                "metadata": metadata or {},
            }
            for node_id, content, metadata in zip(
                ids,
                documents,
                metadatas,
            )
        ]
