"""授权检索应用编排。

本模块只组织端口和纯算法，不创建 Chroma、DashScope、ORM 或模型 SDK。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Callable

from ..core import (
    AccessPrincipal,
    AuditRecorder,
    KnowledgeAccessPolicy,
    Reranker,
)
from .retrieval import HybridResultSelector


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchRuntimeConfig:
    dense_k: int
    lexical_k: int
    union_limit: int
    final_k: int
    score_threshold: float
    hybrid_enabled: bool
    bm25_enabled: bool
    acl_pushdown_enabled: bool
    prompt_version: str


class AuthorizedRetrievalPipeline:
    """执行 ACL 下推、Dense/BM25 融合、精排、装箱和审计。"""

    def __init__(
        self,
        *,
        knowledge,
        access_policy: KnowledgeAccessPolicy,
        reranker: Reranker,
        audit_recorder: AuditRecorder,
        selector_factory: Callable[[], HybridResultSelector],
        context_packer: Callable[..., object],
    ) -> None:
        self.knowledge = knowledge
        self.access_policy = access_policy
        self.reranker = reranker
        self.audit_recorder = audit_recorder
        self.selector_factory = selector_factory
        self.context_packer = context_packer

    async def retrieve(
        self,
        query: str,
        *,
        principal: AccessPrincipal,
        config: SearchRuntimeConfig,
        audit_context: dict | None = None,
    ):
        started_at = time.perf_counter()
        query_id = hashlib.sha256((query or "").encode("utf-8")).hexdigest()[:12]
        try:
            allowed_doc_ids = await asyncio.to_thread(
                self.knowledge.allowed_document_ids, principal
            )
            if not allowed_doc_ids:
                return self.context_packer([])

            dense = await self.knowledge.asearch(
                query,
                top_k=config.dense_k,
                allowed_doc_ids=(
                    allowed_doc_ids if config.acl_pushdown_enabled else None
                ),
            )
            # Chroma where 是性能优化，服务端二次过滤才是权限边界。
            dense = self.access_policy.filter(dense, principal)
            lexical: list[dict] = []
            if config.hybrid_enabled and config.bm25_enabled:
                try:
                    lexical = await self.knowledge.alexical_search(
                        query,
                        top_k=config.lexical_k,
                        allowed_doc_ids=allowed_doc_ids,
                    )
                    lexical = self.access_policy.filter(lexical, principal)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning(
                        "RAG BM25 分支异常，已降级为 dense: query_id=%s",
                        query_id,
                        exc_info=True,
                    )

            candidates, selection_stats = self.selector_factory().fuse_ranked(
                dense,
                lexical,
                limit=config.union_limit,
            )
            results, rerank_stats = await self.reranker.rerank(
                query,
                candidates,
                top_k=config.final_k,
            )
            packed = self.context_packer(results, query=query)
            elapsed_ms = round((time.perf_counter() - started_at) * 1000, 1)
            release_id = self.knowledge.current_release_id()
            citation_map = {
                entry.citation_id: {
                    "node_id": entry.node_id,
                    "source": entry.source,
                    "section_path": entry.heading_path,
                    "position": entry.position,
                    "release_id": release_id,
                }
                for entry in packed.entries
            }
            trace_id = await self.audit_recorder.record({
                **dict(audit_context or {}),
                "principal_role": principal.role,
                "principal_id": principal.user_id,
                "query_hash": hashlib.sha256(query.encode("utf-8")).hexdigest(),
                "mode": "knowledge_base",
                "release_id": release_id,
                "status": "retrieved" if packed.entries else "no_knowledge",
                "embedding_provider": getattr(
                    self.knowledge, "embedding_provider", None
                ),
                "embedding_model": getattr(self.knowledge, "embedding_model", None),
                "embedding_schema_version": getattr(
                    self.knowledge, "embedding_schema_version", None
                ),
                "prompt_version": config.prompt_version,
                "reranker_model": rerank_stats.get("model"),
                "retrieval_config": {
                    "dense_k": config.dense_k,
                    "lexical_k": config.lexical_k,
                    "union_limit": config.union_limit,
                    "final_k": config.final_k,
                    "score_threshold": config.score_threshold,
                    "acl_pushdown": config.acl_pushdown_enabled,
                    "selection_mode": selection_stats.get("mode"),
                    "rerank_mode": rerank_stats.get("mode"),
                },
                "candidate_counts": {
                    "authorized_documents": len(allowed_doc_ids),
                    "dense": len(dense),
                    "bm25": len(lexical),
                    "union": len(candidates),
                    "final": len(results),
                    "packed": len(packed.entries),
                },
                "stage_durations_ms": {"retrieval_total": elapsed_ms},
                "token_usage": {
                    "context_tokens_estimated": packed.token_count,
                    "provider_usage_available": False,
                },
                "candidates": [self.audit_candidate(item) for item in candidates],
                "citation_map": citation_map,
            })
            if audit_context is not None:
                audit_context["_trace_id"] = trace_id
                audit_context["_retrieval_elapsed_ms"] = elapsed_ms
            logger.info(
                "RAG 授权检索完成: query_id=%s release_id=%s elapsed_ms=%s "
                "authorized_docs=%s dense=%s bm25=%s union=%s final=%s rerank=%s",
                query_id,
                release_id,
                elapsed_ms,
                len(allowed_doc_ids),
                len(dense),
                len(lexical),
                selection_stats.get("final"),
                len(results),
                rerank_stats.get("mode"),
            )
            return packed
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "RAG 授权检索失败并进入无知识拒答: query_id=%s elapsed_ms=%s",
                query_id,
                round((time.perf_counter() - started_at) * 1000, 1),
            )
            return self.context_packer([])

    @staticmethod
    def audit_candidate(item: dict) -> dict:
        return {
            key: item.get(key)
            for key in (
                "node_id", "doc_id", "filename", "chunk_index",
                "score", "distance", "bm25_score", "fusion_score",
                "rerank_score", "dense_rank", "bm25_rank", "source_channels",
            )
            if item.get(key) is not None
        }


__all__ = ["AuthorizedRetrievalPipeline", "SearchRuntimeConfig"]
