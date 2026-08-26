"""阶段 1 PaperDocument v2 候选构建；不创建或发布向量索引。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping, Sequence

from ..core.contracts import SourceInfo
from .paper_model import deterministic_paper_document_id
from .routing import ParserRouter
from .storage import KnowledgeArtifactRepository, sha256_bytes, utc_now_iso


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass
class PaperCorpusCandidateBuilder:
    repository: KnowledgeArtifactRepository
    router: ParserRouter

    def build(self, entries: Sequence[Mapping[str, object]]) -> dict:
        items = list(entries)
        if not items:
            raise ValueError("PaperDocument 候选语料不能为空")
        filenames = [str(item.get("filename") or "") for item in items]
        if any(not value for value in filenames) or len(filenames) != len(set(filenames)):
            raise ValueError("PaperDocument 候选文件名为空或重复")
        pointer_before = self.repository.releases.active()
        documents = []
        for item in items:
            filename = str(item["filename"])
            raw = bytes(item.get("file_bytes") or b"")
            if not raw:
                raise ValueError(f"文件内容为空: {filename}")
            actual_hash = sha256_bytes(raw)
            expected_hash = str(item.get("sha256") or actual_hash)
            if actual_hash != expected_hash:
                raise ValueError(f"冻结文件 SHA256 不一致: {filename}")
            extension = "." + filename.rsplit(".", 1)[-1].lower()
            source = self.repository.files.put(raw, filename, extension)
            document_id = deterministic_paper_document_id(source)
            reused = False
            try:
                record = self.repository.paper_documents.get(document_id)
                if record["source"]["sha256"] != source.sha256:
                    raise RuntimeError("PaperDocument v2 来源哈希冲突")
                reused = True
            except FileNotFoundError:
                result = self.router.parse(
                    raw,
                    filename,
                    source=source,
                    work_id=str(item.get("work_id") or ""),
                    catalog_metadata=item.get("catalog_metadata") or {},
                )
                record = self.repository.paper_documents.put(
                    result.paper_document
                )
            diagnostics = dict(record.get("diagnostics") or {})
            documents.append({
                "work_id": record.get("work_id") or "",
                "filename": filename,
                "source_sha256": source.sha256,
                "paper_document_id": document_id,
                "normalized_markdown_sha256": record[
                    "normalized_markdown_sha256"
                ],
                "block_ids_sha256": record["block_ids_sha256"],
                "block_count": len(record.get("blocks") or []),
                "quality_status": diagnostics.get("quality_status"),
                "publish_eligible": diagnostics.get("publish_eligible"),
                "fallback_used": diagnostics.get("fallback_used"),
                "primary_parser": diagnostics.get("primary_parser"),
                "grobid_status": diagnostics.get("grobid_status"),
                "warnings": diagnostics.get("warnings") or [],
                "reused": reused,
            })
        pointer_after = self.repository.releases.active()
        if pointer_after != pointer_before:
            raise RuntimeError("阶段 1 候选构建意外修改了活动 release")
        candidate_identity = [{
            "paper_document_id": item["paper_document_id"],
            "block_ids_sha256": item["block_ids_sha256"],
        } for item in documents]
        return {
            "schema_version": "paper-document-candidate-v1",
            "status": "staged_not_published",
            "created_at": utc_now_iso(),
            "candidate_id": _canonical_sha256(candidate_identity)[:32],
            "active_release_before": pointer_before,
            "active_release_after": pointer_after,
            "document_count": len(documents),
            "publish_eligible_documents": sum(
                bool(item["publish_eligible"]) for item in documents
            ),
            "degraded_documents": sum(
                item["quality_status"] == "degraded" for item in documents
            ),
            "blocked_documents": sum(
                item["quality_status"] == "blocked" for item in documents
            ),
            "documents": documents,
        }


__all__ = ["PaperCorpusCandidateBuilder"]
