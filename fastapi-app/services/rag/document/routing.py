"""论文解析路由：Docling 主解析、GROBID 补充、MarkItDown 受控回退。"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from typing import Mapping

from ..core.contracts import Document, DocumentLoader, DocumentPreprocessor, SourceInfo
from .docling_loader import DoclingPaperLoader
from .grobid import GrobidMetadataEnricher
from .paper_model import PAPER_PARSER_PROFILE, PaperDocument, PaperDocumentNormalizer


@dataclass(frozen=True)
class PaperParseResult:
    paper_document: PaperDocument
    diagnostics: Mapping[str, object]


def probe_pdf(file_bytes: bytes) -> dict[str, object]:
    """用轻量文本探针识别页数和疑似扫描页，不替代版面解析。"""

    try:
        from PyPDF2 import PdfReader

        reader = PdfReader(io.BytesIO(file_bytes))
        page_chars = []
        for page in reader.pages:
            try:
                page_chars.append(len((page.extract_text() or "").strip()))
            except Exception:
                page_chars.append(0)
        return {
            "page_count": len(reader.pages),
            "ocr_page_count": sum(value < 20 for value in page_chars),
            "extractable_text_chars": sum(page_chars),
            "pdf_probe_status": "completed",
        }
    except Exception as exc:
        return {
            "page_count": None,
            "ocr_page_count": None,
            "extractable_text_chars": None,
            "pdf_probe_status": "failed",
            "pdf_probe_error": type(exc).__name__,
        }


class ParserRouter:
    def __init__(
        self,
        *,
        fallback_loader: DocumentLoader,
        preprocessor: DocumentPreprocessor,
        docling_loader: DoclingPaperLoader | None = None,
        grobid_enricher: GrobidMetadataEnricher | None = None,
        normalizer: PaperDocumentNormalizer | None = None,
        preferred_pdf_parser: str = "docling",
    ):
        self.fallback_loader = fallback_loader
        self.preprocessor = preprocessor
        self.docling_loader = docling_loader or DoclingPaperLoader()
        self.grobid_enricher = grobid_enricher or GrobidMetadataEnricher(
            enabled=False
        )
        self.normalizer = normalizer or PaperDocumentNormalizer()
        if preferred_pdf_parser not in {"docling", "markitdown"}:
            raise ValueError("论文解析器只允许 docling 或 markitdown")
        self.preferred_pdf_parser = preferred_pdf_parser

    def parse(
        self,
        file_bytes: bytes,
        filename: str,
        *,
        source: SourceInfo,
        work_id: str = "",
        catalog_metadata: Mapping[str, object] | None = None,
    ) -> PaperParseResult:
        extension = os.path.splitext(filename)[1].lower()
        parser_metadata: Mapping[str, object] = {}
        warnings: list[str] = []
        quality_hints = list(
            (catalog_metadata or {}).get("quality_hints") or []
        )
        source_manual_review = "manual_visual_review_required" in quality_hints
        if source_manual_review:
            warnings.append("冻结语料已标记 PDF 字体映射异常，需要人工视觉复核")
        fallback_used = False
        probe = probe_pdf(file_bytes) if extension == ".pdf" else {
            "page_count": None,
            "ocr_page_count": 0,
            "pdf_probe_status": "not_applicable",
        }

        if (
            extension == ".pdf"
            and self.preferred_pdf_parser == "docling"
            and self.docling_loader.available
        ):
            try:
                loaded = self.docling_loader.load(file_bytes, filename)
                document = loaded.document
                parser_metadata = loaded.metadata
                parser_diagnostics = dict(loaded.diagnostics)
                primary_parser = "docling"
            except Exception as exc:
                fallback_used = True
                warnings.append(
                    f"Docling 解析失败，受控回退 MarkItDown: {type(exc).__name__}"
                )
                loaded_document = self.fallback_loader.load(file_bytes, filename)
                document, parser_diagnostics = self.preprocessor.process(
                    loaded_document
                )
                primary_parser = "markitdown"
        else:
            fallback_used = extension == ".pdf" and self.preferred_pdf_parser == "docling"
            if fallback_used:
                warnings.append("Docling 不可用，受控回退 MarkItDown")
            loaded_document = self.fallback_loader.load(file_bytes, filename)
            document, parser_diagnostics = self.preprocessor.process(loaded_document)
            primary_parser = "markitdown"

        grobid = (
            self.grobid_enricher.enrich(file_bytes, filename)
            if extension == ".pdf" else None
        )
        grobid_metadata = grobid.metadata if grobid else {}
        grobid_diagnostics = dict(grobid.diagnostics) if grobid else {
            "metadata_enricher": "not_applicable",
            "grobid_status": "not_applicable",
        }
        warnings.extend(grobid_diagnostics.get("warnings") or [])
        page_count = probe.get("page_count") or parser_diagnostics.get("page_count")
        cleaned_chars = len(document.text.strip())
        blocking_errors = []
        if cleaned_chars == 0:
            blocking_errors.append("转换后正文为空")
        if page_count and cleaned_chars < max(200, int(page_count) * 20):
            blocking_errors.append("正文字符数与页数严重不匹配")
        diagnostics = {
            **dict(parser_diagnostics),
            **probe,
            **grobid_diagnostics,
            "parser_profile": (
                PAPER_PARSER_PROFILE if extension == ".pdf" else "general_document_v1"
            ),
            "primary_parser": primary_parser,
            "fallback_used": fallback_used,
            "reading_order_confidence": parser_diagnostics.get(
                "reading_order_confidence"
            ),
            "reading_order_confidence_source": parser_diagnostics.get(
                "reading_order_confidence_source", "not_available"
            ),
            "unresolved_blocks": parser_diagnostics.get("unresolved_blocks", 0),
            "warnings": list(dict.fromkeys(str(item) for item in warnings)),
            "blocking_errors": blocking_errors,
            "quality_status": (
                "blocked" if blocking_errors else "degraded" if warnings else "passed"
            ),
            "publish_eligible": not blocking_errors,
            "manual_review_required": source_manual_review,
        }
        normalized = self.normalizer.normalize(
            Document(
                text=document.text,
                metadata=document.metadata,
                document_id=document.document_id,
                source=source,
            ),
            source=source,
            work_id=work_id,
            source_parser=primary_parser,
            parser_metadata=parser_metadata,
            grobid_metadata=grobid_metadata,
            catalog_metadata=catalog_metadata,
            diagnostics=diagnostics,
        )
        return PaperParseResult(normalized, normalized.diagnostics)


__all__ = ["PaperParseResult", "ParserRouter", "probe_pdf"]
