"""召回结果选择与混合排序策略。"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass(frozen=True)
class RetrievalPolicy:
    candidate_k: int
    final_k: int
    score_threshold: float
    hybrid_enabled: bool
    lexical_min_score: float


class HybridResultSelector:
    """纯排序策略；不访问向量库，可独立替换为框架 Retriever。"""

    def __init__(self, policy: RetrievalPolicy):
        self.policy = policy

    @staticmethod
    def normalized_content(content: str) -> str:
        return re.sub(r"\s+", "", (content or "").lower())

    @staticmethod
    def result_score(result: dict) -> float:
        try:
            return float(result.get("score", -1.0))
        except (TypeError, ValueError):
            return -1.0

    @classmethod
    def is_near_duplicate(cls, candidate: dict, selected: list) -> bool:
        candidate_text = cls.normalized_content(candidate.get("content", ""))
        if not candidate_text:
            return True
        for existing in selected:
            if (
                candidate.get("doc_id")
                and candidate.get("doc_id") == existing.get("doc_id")
                and candidate.get("chunk_index") is not None
                and candidate.get("chunk_index") == existing.get("chunk_index")
            ):
                return True
            existing_text = cls.normalized_content(existing.get("content", ""))
            if candidate_text == existing_text:
                return True
            if min(len(candidate_text), len(existing_text)) >= 40:
                similarity = SequenceMatcher(
                    None, candidate_text, existing_text, autojunk=False
                ).ratio()
                if similarity >= 0.88:
                    return True
        return False

    @staticmethod
    def query_features(text: str) -> set[str]:
        lowered = (text or "").lower()
        latin = set(re.findall(r"[a-z][a-z0-9_.+:/\\-]{1,}", lowered))
        chinese_runs = re.findall(r"[\u3400-\u9fff]+", lowered)
        cjk_bigrams = {
            run[index:index + 2]
            for run in chinese_runs
            for index in range(max(0, len(run) - 1))
        }
        return latin | cjk_bigrams

    @classmethod
    def lexical_score(cls, query: str, document: str) -> float:
        features = cls.query_features(query)
        if not features:
            return 0.0
        lowered = (document or "").lower()
        matched = 0.0
        possible = 0.0
        for feature in features:
            weight = 3.0 if re.search(r"[a-z0-9]", feature) else 1.0
            possible += weight
            if feature in lowered:
                matched += weight
        return matched / possible if possible else 0.0

    @staticmethod
    def result_key(item: dict) -> str:
        if item.get("doc_id") is not None and item.get("chunk_index") is not None:
            return f"{item['doc_id']}:{item['chunk_index']}"
        digest = hashlib.sha256(
            str(item.get("content") or "").encode("utf-8")
        ).hexdigest()[:16]
        return f"content:{digest}"

    def select_dense(self, candidates: list) -> tuple[list, dict]:
        ordered = sorted(candidates or [], key=self.result_score, reverse=True)
        threshold_passed = [
            item for item in ordered
            if self.result_score(item) >= self.policy.score_threshold
        ]
        deduplicated = []
        for item in threshold_passed:
            if not self.is_near_duplicate(item, deduplicated):
                deduplicated.append(item)
        selected = deduplicated[:self.policy.final_k]
        return selected, {
            "mode": "dense",
            "candidates": len(ordered),
            "threshold_passed": len(threshold_passed),
            "lexical_candidates": 0,
            "deduplicated": len(deduplicated),
            "final": len(selected),
        }

    def select_hybrid(
        self,
        query: str,
        dense_candidates: list,
        all_nodes: list,
    ) -> tuple[list, dict]:
        ordered_dense = sorted(
            dense_candidates or [], key=self.result_score, reverse=True
        )
        threshold_dense = [
            item for item in ordered_dense
            if self.result_score(item) >= self.policy.score_threshold
        ]
        lexical_ranked = sorted(
            (
                (self.lexical_score(query, item.get("content", "")), item)
                for item in (all_nodes or [])
            ),
            key=lambda pair: pair[0],
            reverse=True,
        )
        lexical_ranked = [
            pair for pair in lexical_ranked
            if pair[0] >= self.policy.lexical_min_score
        ][:self.policy.candidate_k]
        fused: dict[str, dict] = {}
        rrf_scores = defaultdict(float)
        lexical_scores = {}
        for rank, item in enumerate(threshold_dense, start=1):
            key = self.result_key(item)
            fused[key] = dict(item)
            rrf_scores[key] += 1.0 / (60 + rank)
        for rank, (lexical_score, item) in enumerate(lexical_ranked, start=1):
            key = self.result_key(item)
            fused.setdefault(key, dict(item))
            lexical_scores[key] = lexical_score
            rrf_scores[key] += 1.0 / (60 + rank)
        ranked = sorted(
            fused.items(),
            key=lambda pair: (rrf_scores[pair[0]], self.result_score(pair[1])),
            reverse=True,
        )
        deduplicated = []
        for key, item in ranked:
            item["fusion_score"] = rrf_scores[key]
            if key in lexical_scores:
                item["lexical_score"] = lexical_scores[key]
            if not self.is_near_duplicate(item, deduplicated):
                deduplicated.append(item)
        selected = deduplicated[:self.policy.final_k]
        return selected, {
            "mode": "hybrid",
            "candidates": len(ordered_dense),
            "threshold_passed": len(threshold_dense),
            "lexical_candidates": len(lexical_ranked),
            "deduplicated": len(deduplicated),
            "final": len(selected),
        }

    def fuse_ranked(
        self,
        dense_candidates: list,
        lexical_candidates: list,
        *,
        limit: int,
    ) -> tuple[list, dict]:
        """融合已由向量库和 BM25 排序的候选，不再遍历全库正文。"""

        dense = [
            item for item in sorted(
                dense_candidates or [], key=self.result_score, reverse=True
            )
            if self.result_score(item) >= self.policy.score_threshold
        ]
        lexical = sorted(
            lexical_candidates or [],
            key=lambda item: float(item.get("bm25_score") or 0.0),
            reverse=True,
        )
        fused: dict[str, dict] = {}
        scores = defaultdict(float)
        for channel, values in (("dense", dense), ("bm25", lexical)):
            for rank, item in enumerate(values, start=1):
                key = self.result_key(item)
                target = fused.setdefault(key, dict(item))
                target.update({k: v for k, v in item.items() if v is not None})
                target[f"{channel}_rank"] = rank
                channels = set(target.get("source_channels") or [])
                channels.add(channel)
                target["source_channels"] = sorted(channels)
                scores[key] += 1.0 / (60 + rank)
        ranked = sorted(
            fused.items(),
            key=lambda pair: (scores[pair[0]], self.result_score(pair[1])),
            reverse=True,
        )
        selected = []
        for key, item in ranked:
            item["fusion_score"] = scores[key]
            if not self.is_near_duplicate(item, selected):
                selected.append(item)
            if len(selected) >= limit:
                break
        return selected, {
            "mode": "dense_bm25_rrf",
            "candidates": len(dense_candidates or []),
            "threshold_passed": len(dense),
            "lexical_candidates": len(lexical),
            "deduplicated": len(selected),
            "final": len(selected),
        }
