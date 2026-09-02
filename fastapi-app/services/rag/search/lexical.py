"""Release 级 BM25 倒排索引。

索引只在发布 collection 变更时重建；查询只访问命中词的
postings，不再为每个请求遍历全部 Chroma 正文。
"""

from __future__ import annotations

import math
import re
import threading
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Callable, Iterable


_LATIN_RE = re.compile(r"[a-z][a-z0-9_.+:/\\-]*|\d+(?:\.\d+)?", re.I)
_CJK_RE = re.compile(r"[\u3400-\u9fff]+")


def tokenize(text: str) -> tuple[str, ...]:
    """保留命令/路径精确词，中文以二元组作为无外部分词依赖的稳定基线。"""

    normalized = unicodedata.normalize("NFKC", str(text or "")).lower()
    values = list(_LATIN_RE.findall(normalized))
    for run in _CJK_RE.findall(normalized):
        if len(run) == 1:
            values.append(run)
        else:
            values.extend(run[index:index + 2] for index in range(len(run) - 1))
    return tuple(values)


@dataclass(frozen=True)
class BM25Policy:
    k1: float = 1.5
    b: float = 0.75


class BM25Index:
    """支持授权 doc_id 过滤的稀疏 BM25 索引。"""

    def __init__(self, records: Iterable[dict], policy: BM25Policy | None = None):
        self.policy = policy or BM25Policy()
        self.records = [dict(item) for item in records]
        self.lengths: list[int] = []
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for index, record in enumerate(self.records):
            terms = tokenize(
                f"{record.get('section_path') or record.get('heading_path') or ''} "
                f"{record.get('content') or ''}"
            )
            counts = Counter(terms)
            self.lengths.append(max(1, len(terms)))
            for term, frequency in counts.items():
                self.postings[term].append((index, frequency))
        self.average_length = (
            sum(self.lengths) / len(self.lengths) if self.lengths else 1.0
        )

    def search(
        self,
        query: str,
        *,
        top_k: int,
        allowed_doc_ids: set[str] | None = None,
    ) -> list[dict]:
        if top_k <= 0 or not self.records:
            return []
        scores: dict[int, float] = defaultdict(float)
        query_terms = Counter(tokenize(query))
        total = len(self.records)
        for term, query_frequency in query_terms.items():
            posting = self.postings.get(term) or ()
            if not posting:
                continue
            document_frequency = len(posting)
            idf = math.log(1.0 + (total - document_frequency + 0.5) / (
                document_frequency + 0.5
            ))
            for index, frequency in posting:
                record = self.records[index]
                if allowed_doc_ids is not None and str(record.get("doc_id")) not in allowed_doc_ids:
                    continue
                length = self.lengths[index]
                denominator = frequency + self.policy.k1 * (
                    1.0 - self.policy.b
                    + self.policy.b * length / self.average_length
                )
                scores[index] += query_frequency * idf * (
                    frequency * (self.policy.k1 + 1.0) / denominator
                )
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        return [
            {**self.records[index], "bm25_score": float(score)}
            for index, score in ranked[:top_k]
            if score > 0
        ]


class ReleaseBM25Cache:
    """每个进程只保留当前 release 的一份不可变索引。

    Release ID 与正文加载必须分离：命中缓存时只读取本地发布指针，不能为
    了确认缓存而再次全量读取向量库。构建过程放在同一把锁内，避免并发冷
    请求重复加载快照。
    """

    def __init__(
        self,
        release_id_provider: Callable[[], str],
        snapshot_provider: Callable[[str], list[dict]],
    ):
        self._release_id_provider = release_id_provider
        self._snapshot_provider = snapshot_provider
        self._lock = threading.RLock()
        self._release_id: str | None = None
        self._index: BM25Index | None = None

    def index(self) -> tuple[str, BM25Index]:
        with self._lock:
            # 等待并发构建锁之后重新读取指针，避免线程排队期间发生发布切换，
            # 却按进入锁之前读取的旧 ID 错误命中旧缓存。
            release_id = self._release_id_provider()
            if self._index is not None and self._release_id == release_id:
                return release_id, self._index

            # 发布指针可能在本地快照加载期间切换。只有快照仍对应当前指针时
            # 才安装缓存；若发生切换则重试新 release，避免混用版本。
            for _ in range(3):
                records = self._snapshot_provider(release_id)
                current_release_id = self._release_id_provider()
                if current_release_id == release_id:
                    self._index = BM25Index(records)
                    self._release_id = release_id
                    return release_id, self._index
                release_id = current_release_id

            raise RuntimeError("BM25 快照构建期间发布指针持续变化")

    def search(
        self,
        query: str,
        *,
        top_k: int,
        allowed_doc_ids: set[str] | None,
    ) -> tuple[str, list[dict]]:
        release_id, index = self.index()
        return release_id, index.search(
            query, top_k=top_k, allowed_doc_ids=allowed_doc_ids
        )


__all__ = ["BM25Index", "BM25Policy", "ReleaseBM25Cache", "tokenize"]
