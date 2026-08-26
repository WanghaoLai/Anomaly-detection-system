"""从冻结 15 篇论文构建未发布 PaperDocument v2 候选。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "fastapi-app"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from markitdown import MarkItDown, StreamInfo  # noqa: E402
from services.rag.document import (  # noqa: E402
    DoclingPaperLoader,
    GrobidMetadataEnricher,
    KnowledgeArtifactRepository,
    MarkItDownDocumentLoader,
    PaperCorpusCandidateBuilder,
    PaperDocumentPreprocessor,
    ParserRouter,
)
from settings import AI_CONFIG  # noqa: E402


DEFAULT_MANIFEST = PROJECT_ROOT / "config" / "rag_multi_paper_corpus_v1.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "reports" / "rag_paper_documents_v2_candidate.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _catalog_metadata(item: dict) -> dict:
    return {
        "title": item.get("title"),
        "authors": item.get("authors") or [],
        "publication_year": item.get("publication_year"),
        "language": item.get("language"),
        "doi": item.get("doi"),
        "arxiv_id": item.get("arxiv_id"),
        "quality_hints": item.get("diagnostics") or [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=Path(str(AI_CONFIG["rag_artifact_path"])),
    )
    args = parser.parse_args()
    manifest_path = args.manifest.resolve()
    source_dir = args.source_dir.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    documents = list(manifest.get("documents") or [])
    if len(documents) != 15:
        raise ValueError("阶段 1 冻结语料必须恰好为 15 篇")
    expected_names = {item["filename"] for item in documents}
    actual_names = {path.name for path in source_dir.glob("*.pdf")}
    if expected_names != actual_names:
        raise ValueError("论文目录与冻结清单不一致")

    converter = MarkItDown(enable_plugins=False)
    fallback = MarkItDownDocumentLoader(lambda: converter, StreamInfo)
    router = ParserRouter(
        fallback_loader=fallback,
        preprocessor=PaperDocumentPreprocessor(),
        docling_loader=DoclingPaperLoader(
            ocr_enabled=bool(AI_CONFIG.get("rag_ocr_enabled", True))
        ),
        grobid_enricher=GrobidMetadataEnricher(
            base_url=str(AI_CONFIG.get("rag_grobid_url") or ""),
            enabled=bool(AI_CONFIG.get("rag_grobid_enabled", True)),
            timeout_seconds=float(
                AI_CONFIG.get("rag_grobid_timeout_seconds", 30.0)
            ),
        ),
        preferred_pdf_parser=str(AI_CONFIG.get("rag_paper_parser") or "docling"),
    )
    entries = []
    for item in documents:
        path = source_dir / item["filename"]
        if path.stat().st_size != item["byte_size"] or _sha256(path) != item["sha256"]:
            raise ValueError(f"冻结论文漂移: {item['filename']}")
        entries.append({
            "filename": item["filename"],
            "file_bytes": path.read_bytes(),
            "sha256": item["sha256"],
            "work_id": item["work_id"],
            "catalog_metadata": _catalog_metadata(item),
        })
    repository = KnowledgeArtifactRepository(args.artifact_root.resolve())
    report = PaperCorpusCandidateBuilder(repository, router).build(entries)
    report.update({
        "corpus_id": manifest["corpus_id"],
        "corpus_manifest_sha256": _sha256(manifest_path),
        "parser_requested": AI_CONFIG.get("rag_paper_parser"),
        "docling_available": router.docling_loader.available,
        "grobid_configured": bool(AI_CONFIG.get("rag_grobid_url")),
    })
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": report["status"],
        "candidate_id": report["candidate_id"],
        "documents": report["document_count"],
        "eligible": report["publish_eligible_documents"],
        "degraded": report["degraded_documents"],
        "blocked": report["blocked_documents"],
        "docling_available": report["docling_available"],
        "grobid_configured": report["grobid_configured"],
        "output": str(output),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
