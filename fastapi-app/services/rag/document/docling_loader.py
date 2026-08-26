"""Docling PDF 适配器；依赖惰性加载，厂商类型不越过本模块。"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import io
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from ..core.contracts import Document
from .loading import safe_filename


class DoclingUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class DoclingLoadResult:
    document: Document
    metadata: Mapping[str, object]
    diagnostics: Mapping[str, object]


def _walk_labels(value: object) -> list[str]:
    labels: list[str] = []
    if isinstance(value, Mapping):
        label = value.get("label") or value.get("type")
        if label:
            labels.append(str(label).casefold())
        for child in value.values():
            labels.extend(_walk_labels(child))
    elif isinstance(value, list):
        for child in value:
            labels.extend(_walk_labels(child))
    return labels


class DoclingPaperLoader:
    def __init__(
        self,
        converter_provider: Callable[[], object] | None = None,
        stream_factory: Callable[[str, bytes], object] | None = None,
        *,
        ocr_enabled: bool = True,
    ):
        self._converter_provider = converter_provider
        self._stream_factory = stream_factory
        self.ocr_enabled = bool(ocr_enabled)

    @property
    def available(self) -> bool:
        return self._converter_provider is not None or (
            importlib.util.find_spec("docling") is not None
            and importlib.util.find_spec("docling_core") is not None
        )

    @staticmethod
    def version() -> str | None:
        try:
            return importlib.metadata.version("docling")
        except importlib.metadata.PackageNotFoundError:
            return None

    def _converter(self) -> object:
        if self._converter_provider is not None:
            return self._converter_provider()
        if not self.available:
            raise DoclingUnavailableError(
                "Docling 未安装；请安装 requirements-paper-rag.txt"
            )
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        pipeline_options = PdfPipelineOptions(do_ocr=self.ocr_enabled)
        return DocumentConverter(format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=pipeline_options
            )
        })

    def _stream(self, filename: str, file_bytes: bytes) -> object:
        if self._stream_factory is not None:
            return self._stream_factory(filename, file_bytes)
        from docling.datamodel.base_models import DocumentStream

        return DocumentStream(name=filename, stream=io.BytesIO(file_bytes))

    def load(self, file_bytes: bytes, filename: str) -> DoclingLoadResult:
        source_filename = safe_filename(filename)
        if not source_filename.lower().endswith(".pdf"):
            raise ValueError("DoclingPaperLoader 只接受 PDF")
        if not file_bytes:
            raise ValueError("文件内容为空")
        converter = self._converter()
        result = converter.convert(self._stream(source_filename, bytes(file_bytes)))
        native_document = getattr(result, "document", None)
        if native_document is None:
            raise ValueError("Docling 未返回 document")
        markdown = native_document.export_to_markdown(
            page_break_placeholder="\n\f\n"
        )
        if not isinstance(markdown, str) or not markdown.strip():
            raise ValueError("Docling 转换后无有效 Markdown")
        exported = (
            native_document.export_to_dict()
            if hasattr(native_document, "export_to_dict") else {}
        )
        labels = _walk_labels(exported)
        pages = exported.get("pages") if isinstance(exported, Mapping) else None
        page_count = len(pages) if isinstance(pages, Mapping | list) else None
        diagnostics = {
            "docling_version": self.version() or "injected-test-adapter",
            "page_count": page_count,
            "reading_order_confidence": None,
            "reading_order_confidence_source": "not_reported_by_adapter",
            "ocr_enabled": self.ocr_enabled,
            "table_count": sum("table" in label for label in labels),
            "figure_count": sum(
                "picture" in label or "figure" in label for label in labels
            ),
            "formula_count": sum("formula" in label for label in labels),
            "unresolved_blocks": 0,
        }
        metadata = {}
        if isinstance(exported, Mapping):
            for key in ("title", "authors", "language"):
                if exported.get(key):
                    metadata[key] = exported[key]
        return DoclingLoadResult(
            document=Document(
                text=markdown.strip(),
                metadata={
                    "filename": source_filename,
                    "extension": ".pdf",
                    "content_format": "markdown",
                    "converter": "docling",
                },
            ),
            metadata=metadata,
            diagnostics=diagnostics,
        )


__all__ = [
    "DoclingLoadResult",
    "DoclingPaperLoader",
    "DoclingUnavailableError",
]
