"""文档加载与标准化组件。"""

from __future__ import annotations

import io
import logging
import math
import os
import re
from collections import Counter
from typing import Callable, Optional

from ..core.contracts import Document, SourceInfo
from .markdown import MARKDOWN_FENCE_RE, MARKDOWN_HEADING_RE

logger = logging.getLogger(__name__)

SUPPORTED_DOCUMENT_EXTENSIONS = frozenset({
    ".txt", ".md", ".markdown", ".pdf", ".docx", ".pptx", ".xlsx",
    ".xls", ".csv", ".html", ".htm", ".json", ".xml", ".ipynb",
    ".epub",
})

_PDF_PAGE_NUMBER_RE = re.compile(
    r"^\s*(?:第\s*\d+\s*页|page\s*\d+(?:\s*(?:/|of)\s*\d+)?)\s*$",
    re.IGNORECASE,
)
_NUMBERED_TITLE_RE = re.compile(
    r"^\s*(?P<number>\d+(?:\.\d+){0,4})[.、]?\s+(?P<title>\S.*?)\s*$"
)
_CHINESE_TITLE_RE = re.compile(
    r"^\s*(?:(?:第[一二三四五六七八九十百零〇\d]+[章节篇部])|"
    r"(?:[一二三四五六七八九十]+、))\s*(?P<title>\S.*?)\s*$"
)


def safe_filename(filename: str) -> str:
    return os.path.basename((filename or "").replace("\\", "/"))


class MarkItDownDocumentLoader:
    """把所有支持格式统一加载为 Markdown Document。"""

    def __init__(
        self,
        converter_provider: Callable[[], object],
        stream_info_factory: Optional[Callable[..., object]] = None,
        pdf_ocr=None,
        pdf_ocr_min_chars: int = 200,
        pdf_ocr_min_chars_per_page: int = 20,
    ):
        self._converter_provider = converter_provider
        self._stream_info_factory = stream_info_factory
        self._pdf_ocr = pdf_ocr
        self._pdf_ocr_min_chars = int(pdf_ocr_min_chars)
        self._pdf_ocr_min_chars_per_page = int(pdf_ocr_min_chars_per_page)

    @staticmethod
    def _visible_chars(text: str) -> int:
        return len(re.sub(r"\s+", "", text or ""))

    def _maybe_ocr_pdf(
        self, file_bytes: bytes, markdown: str, source_filename: str
    ) -> tuple[str, dict]:
        metadata = {
            "ocr_attempted": False,
            "ocr_status": "not_needed",
            "ocr_pages": 0,
        }
        if self._pdf_ocr is None:
            return markdown, metadata
        try:
            page_count = self._pdf_ocr.page_count(file_bytes)
        except Exception as exc:
            logger.warning(
                "PDF 页数检查失败，保持 MarkItDown 结果: filename=%s error=%s",
                source_filename,
                exc,
            )
            metadata.update({"ocr_status": "inspection_failed"})
            return markdown, metadata
        metadata["page_count"] = page_count
        minimum_chars = max(
            self._pdf_ocr_min_chars,
            page_count * self._pdf_ocr_min_chars_per_page,
        )
        if self._visible_chars(markdown) >= minimum_chars:
            return markdown, metadata
        metadata["ocr_attempted"] = True
        try:
            result = self._pdf_ocr.extract(file_bytes)
        except Exception as exc:
            logger.warning(
                "本地 PDF OCR 失败: filename=%s error=%s",
                source_filename,
                exc,
            )
            metadata.update({"ocr_status": "failed", "ocr_error": type(exc).__name__})
            return markdown, metadata
        ocr_text = str(result.text or "").strip()
        if self._visible_chars(ocr_text) <= self._visible_chars(markdown):
            metadata.update({"ocr_status": "no_improvement", "ocr_pages": result.ocr_pages})
            return markdown, metadata
        metadata.update({
            "ocr_status": "applied",
            "ocr_pages": result.ocr_pages,
            "page_count": result.page_count,
            "ocr_engine": getattr(self._pdf_ocr, "engine", "local"),
            "ocr_engine_version": getattr(self._pdf_ocr, "engine_version", "unknown"),
            "ocr_model_family": getattr(self._pdf_ocr, "model_family", "unknown"),
            "ocr_model_version": getattr(self._pdf_ocr, "model_version", "unknown"),
        })
        return ocr_text, metadata

    def load(self, file_bytes: bytes, filename: str) -> Document:
        source_filename = safe_filename(filename)
        extension = os.path.splitext(source_filename)[1].lower()
        if extension not in SUPPORTED_DOCUMENT_EXTENSIONS:
            raise ValueError(f"不支持的文件格式: {extension or '[无扩展名]'}")
        if not file_bytes:
            raise ValueError("文件内容为空")

        if self._stream_info_factory is not None:
            kwargs = {"stream_info": self._stream_info_factory(
                extension=extension,
                filename=source_filename,
            )}
        else:
            kwargs = {"file_extension": extension}
        conversion_error = None
        try:
            result = self._converter_provider().convert_stream(
                io.BytesIO(bytes(file_bytes)),
                **kwargs,
            )
        except Exception as exc:
            logger.warning("MarkItDown 转换失败: filename=%s error=%s", source_filename, exc)
            if extension != ".pdf" or self._pdf_ocr is None:
                raise ValueError(f"文档转换失败: {source_filename}") from exc
            conversion_error = exc
            result = None

        markdown = getattr(result, "markdown", None) if result is not None else None
        if markdown is None and result is not None:
            markdown = getattr(result, "text_content", None)
        markdown = markdown if isinstance(markdown, str) else ""
        ocr_metadata = {}
        if extension == ".pdf":
            markdown, ocr_metadata = self._maybe_ocr_pdf(
                file_bytes, markdown, source_filename
            )
        if not markdown.strip():
            error = ValueError(
                f"文档转换后无有效 Markdown 内容: {source_filename}"
            )
            if conversion_error is not None:
                raise error from conversion_error
            raise error
        return Document(
            text=markdown.strip(),
            metadata={
                "filename": source_filename,
                "extension": extension,
                "content_format": "markdown",
                "converter": (
                    "tesseract_ocr"
                    if ocr_metadata.get("ocr_status") == "applied"
                    else "markitdown"
                ),
                **ocr_metadata,
            },
        )


def _normalize_repeated_pdf_line(line: str) -> str:
    normalized = re.sub(r"\s+", " ", line.strip()).lower()
    return re.sub(r"\d+", "#", normalized)


def _split_pdf_pages(markdown: str) -> tuple[list[list[str]], int]:
    pages: list[list[str]] = []
    current: list[str] = []
    page_markers = 0
    for line in markdown.replace("\f", "\n\f\n").splitlines():
        stripped = line.strip()
        if stripped == "\f" or _PDF_PAGE_NUMBER_RE.match(stripped):
            page_markers += 1
            if any(item.strip() for item in current):
                pages.append(current)
            current = []
            continue
        current.append(line)
    if any(item.strip() for item in current):
        pages.append(current)
    return pages or [markdown.splitlines()], page_markers


def _page_boundary_indexes(lines: list[str], *, from_start: bool) -> list[int]:
    nonempty = [index for index, line in enumerate(lines) if line.strip()]
    return nonempty[:3] if from_start else nonempty[-3:]


def _remove_repeated_pdf_boundaries(
    pages: list[list[str]],
) -> tuple[list[list[str]], list[str], list[str]]:
    if len(pages) < 3:
        return pages, [], []
    header_counter: Counter = Counter()
    footer_counter: Counter = Counter()
    for page in pages:
        for index in _page_boundary_indexes(page, from_start=True):
            line = page[index].strip()
            if 2 <= len(line) <= 120:
                header_counter[_normalize_repeated_pdf_line(line)] += 1
        for index in _page_boundary_indexes(page, from_start=False):
            line = page[index].strip()
            if 2 <= len(line) <= 120:
                footer_counter[_normalize_repeated_pdf_line(line)] += 1

    minimum = max(3, math.ceil(len(pages) * 0.6))
    repeated_headers = {key for key, count in header_counter.items() if count >= minimum}
    repeated_footers = {key for key, count in footer_counter.items() if count >= minimum}
    cleaned_pages: list[list[str]] = []
    seen_headers: set[str] = set()
    removed_headers: list[str] = []
    removed_footers: list[str] = []
    for page in pages:
        header_indexes = set(_page_boundary_indexes(page, from_start=True))
        footer_indexes = set(_page_boundary_indexes(page, from_start=False))
        cleaned_page = []
        for index, line in enumerate(page):
            key = _normalize_repeated_pdf_line(line) if line.strip() else ""
            if index in footer_indexes and key in repeated_footers:
                removed_footers.append(line.strip())
                continue
            if index in header_indexes and key in repeated_headers:
                if key in seen_headers:
                    removed_headers.append(line.strip())
                    continue
                seen_headers.add(key)
            cleaned_page.append(line)
        cleaned_pages.append(cleaned_page)
    return cleaned_pages, removed_headers, removed_footers


def _looks_like_title(title: str, full_line: str) -> bool:
    if not title or len(full_line.strip()) > 80:
        return False
    if full_line.rstrip().endswith(("。", "！", "？", "；", ".", "!", "?", ";", ":", "：")):
        return False
    return not full_line.lstrip().startswith(("|", "- ", "* ", ">", "```", "~~~"))


def _recognize_pdf_titles(markdown: str) -> tuple[str, list[str]]:
    converted: list[str] = []
    detected: list[str] = []
    fence: tuple[str, int] | None = None
    for line in markdown.splitlines():
        fence_match = MARKDOWN_FENCE_RE.match(line)
        if fence is not None:
            converted.append(line)
            if (fence_match and fence_match.group("fence")[0] == fence[0]
                    and len(fence_match.group("fence")) >= fence[1]):
                fence = None
            continue
        if fence_match:
            marker = fence_match.group("fence")
            fence = (marker[0], len(marker))
            converted.append(line)
            continue
        if MARKDOWN_HEADING_RE.match(line):
            converted.append(line)
            detected.append(line.strip())
            continue
        numbered = _NUMBERED_TITLE_RE.match(line)
        if numbered and _looks_like_title(numbered.group("title"), line):
            level = min(6, numbered.group("number").count(".") + 1)
            heading = f"{'#' * level} {line.strip()}"
            converted.append(heading)
            detected.append(heading)
            continue
        chinese = _CHINESE_TITLE_RE.match(line)
        if chinese and _looks_like_title(chinese.group("title"), line):
            heading = f"# {line.strip()}"
            converted.append(heading)
            detected.append(heading)
            continue
        converted.append(line)
    return "\n".join(converted).strip(), detected


def preprocess_pdf_markdown(markdown: str) -> tuple[str, dict]:
    pages, page_markers = _split_pdf_pages(markdown)
    pages, removed_headers, removed_footers = _remove_repeated_pdf_boundaries(pages)
    cleaned = "\n\n".join(
        "\n".join(page).strip() for page in pages if any(line.strip() for line in page)
    )
    enriched, detected_titles = _recognize_pdf_titles(cleaned)
    return enriched, {
        "page_count": len(pages),
        "page_markers_removed": page_markers,
        "headers_removed": len(removed_headers),
        "footers_removed": len(removed_footers),
        "removed_header_samples": list(dict.fromkeys(removed_headers))[:5],
        "removed_footer_samples": list(dict.fromkeys(removed_footers))[:5],
        "detected_title_count": len(detected_titles),
        "detected_titles": detected_titles[:20],
        "raw_char_count": len(markdown),
        "cleaned_char_count": len(enriched),
    }


class DefaultDocumentPreprocessor:
    """执行与文件格式有关的保守清洗，不负责切分。"""

    def process(self, document: Document) -> tuple[Document, dict]:
        extension = str(document.metadata.get("extension") or "").lower()
        raw_markdown = document.text
        if extension == ".pdf":
            markdown, diagnostics = preprocess_pdf_markdown(raw_markdown)
            if document.metadata.get("page_count"):
                diagnostics["page_count"] = int(document.metadata["page_count"])
        else:
            markdown = raw_markdown.strip()
            titles = [
                line.strip() for line in markdown.splitlines()
                if MARKDOWN_HEADING_RE.match(line)
            ]
            diagnostics = {
                "page_count": None,
                "page_markers_removed": 0,
                "headers_removed": 0,
                "footers_removed": 0,
                "removed_header_samples": [],
                "removed_footer_samples": [],
                "detected_title_count": len(titles),
                "detected_titles": titles[:20],
                "raw_char_count": len(raw_markdown),
                "cleaned_char_count": len(markdown),
            }
        if not markdown.strip():
            raise ValueError("文档清理后无有效内容")
        raw_chars = max(1, int(diagnostics.get("raw_char_count") or 0))
        cleaned_chars = int(diagnostics.get("cleaned_char_count") or 0)
        page_count = max(1, int(diagnostics.get("page_count") or 1))
        retention = min(1.0, cleaned_chars / raw_chars)
        # Phase 4A 只观测：每页 200 个清洗字符视作充分文本覆盖，不改变生产拒绝。
        text_coverage = (
            min(1.0, cleaned_chars / (page_count * 200))
            if extension == ".pdf" else 1.0
        )
        minimum_observed_chars = max(200, page_count * 20)
        quality_would_pass = cleaned_chars >= minimum_observed_chars
        quality_warnings = []
        if not quality_would_pass:
            quality_warnings.append("low_text_coverage")
        if extension == ".pdf" and diagnostics.get("detected_title_count") == 0:
            quality_warnings.append("no_detected_titles")
        diagnostics.update({
            "parse_status": "parsed",
            "parse_quality_score": round(
                0.7 * text_coverage + 0.3 * retention, 4
            ),
            "text_coverage": round(text_coverage, 4),
            "text_retention": round(retention, 4),
            "ocr_pages": int(document.metadata.get("ocr_pages") or 0),
            "ocr_status": document.metadata.get("ocr_status", "not_configured"),
            "quality_gate_mode": "observe_only",
            "quality_passed": True,
            "quality_would_pass": quality_would_pass,
            "quality_warnings": quality_warnings,
        })
        # 清洗只允许改变正文，不能丢失 P1 已固化的文档身份和原始来源。
        return Document(
            text=markdown,
            metadata=document.metadata,
            document_id=document.document_id,
            source=document.source,
        ), diagnostics


def prepare_document_worker_payload(payload: dict) -> dict:
    """基础设施 Worker：把 MarkItDown/OCR/LlamaIndex 收敛为纯字典结果。"""
    from markitdown import MarkItDown, StreamInfo

    from .ocr import LocalTesseractPdfOcr
    from .parsing import MarkdownNodeParser

    ocr_config = dict(payload.get("ocr") or {})
    pdf_ocr = None
    if ocr_config.get("enabled"):
        pdf_ocr = LocalTesseractPdfOcr(
            tesseract_path=str(ocr_config["tesseract_path"]),
            pdftoppm_path=str(ocr_config["pdftoppm_path"]),
            pdfinfo_path=str(ocr_config["pdfinfo_path"]),
            tessdata_path=str(ocr_config["tessdata_path"]),
            languages=str(ocr_config["languages"]),
            dpi=int(ocr_config["dpi"]),
            timeout_seconds=float(ocr_config["timeout_seconds"]),
        )
    loader = MarkItDownDocumentLoader(
        lambda: MarkItDown(enable_plugins=False),
        StreamInfo,
        pdf_ocr=pdf_ocr,
        pdf_ocr_min_chars=int(payload["ocr_min_chars"]),
        pdf_ocr_min_chars_per_page=int(payload["ocr_min_chars_per_page"]),
    )
    source_payload = payload.get("source")
    source = SourceInfo(**source_payload) if source_payload else None
    filename = str(payload["filename"])
    extension = os.path.splitext(filename)[1].lower()
    loaded = loader.load(bytes(payload["file_bytes"]), filename)
    document, diagnostics = DefaultDocumentPreprocessor().process(Document(
        text=loaded.text,
        metadata={
            **dict(loaded.metadata),
            "filename": filename,
            "extension": extension,
            "content_format": "markdown",
            "ingestion_schema_version": str(payload["ingestion_schema_version"]),
        },
        document_id=payload.get("document_id"),
        source=source,
    ))
    nodes = MarkdownNodeParser(
        int(payload["chunk_tokens"]), int(payload["overlap_tokens"])
    ).parse(document)
    if not nodes:
        raise ValueError("文档分块后无有效内容")
    chunks = [
        {"content": node.text, "node_id": node.node_id, **dict(node.metadata)}
        for node in nodes
    ]
    diagnostics = dict(diagnostics)
    diagnostics["chunk_count"] = len(chunks)
    diagnostics["average_chunk_tokens"] = round(
        sum(int(chunk.get("token_count") or 0) for chunk in chunks) / len(chunks),
        1,
    )
    return {
        "filename": filename,
        "extension": extension,
        "markdown": document.text,
        "chunks": chunks,
        "diagnostics": diagnostics,
    }
