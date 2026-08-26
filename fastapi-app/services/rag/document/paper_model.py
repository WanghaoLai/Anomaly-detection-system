"""PaperDocument v2 的框架无关模型、规范化和稳定标识。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from ..core.contracts import Document, SourceInfo


PAPER_DOCUMENT_SCHEMA_VERSION = "paper-document-v2"
PAPER_INGESTION_SCHEMA_VERSION = "paper-pdf-docling-grobid-v3"
PAPER_PARSER_PROFILE = "paper_pdf_v1"

_HEADING_RE = re.compile(r"^(?P<marks>#{1,6})\s+(?P<title>.+?)\s*$")
_FIGURE_RE = re.compile(r"^(?:figure|fig\.?|图)\s*\d+", re.IGNORECASE)
_TABLE_RE = re.compile(r"^(?:table|表)\s*\d+", re.IGNORECASE)
_REFERENCE_HEADING_RE = re.compile(
    r"^(?:references|bibliography|参考文献)$", re.IGNORECASE
)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _compact(value: object) -> str:
    return "".join(str(value or "").casefold().split())


def deterministic_paper_document_id(source: SourceInfo) -> str:
    identity = {
        "schema": PAPER_DOCUMENT_SCHEMA_VERSION,
        "ingestion_schema": PAPER_INGESTION_SCHEMA_VERSION,
        "filename": source.filename,
        "source_sha256": source.sha256,
    }
    return hashlib.sha256(_canonical_json(identity)).hexdigest()[:32]


def deterministic_paper_block_id(
    document_id: str,
    block_type: str,
    ordinal: int,
    text: str,
    *,
    section_path: str | None,
    page_start: int | None = None,
    page_end: int | None = None,
    bbox: Sequence[float] | None = None,
) -> str:
    identity = {
        "schema": PAPER_DOCUMENT_SCHEMA_VERSION,
        "document_id": document_id,
        "block_type": block_type,
        "ordinal": int(ordinal),
        "section_path": section_path or "[root]",
        "page_start": page_start,
        "page_end": page_end,
        "bbox": list(bbox) if bbox is not None else None,
        "text_sha256": _sha256_text(text),
    }
    return hashlib.sha256(_canonical_json(identity)).hexdigest()


@dataclass(frozen=True)
class PaperBlock:
    block_id: str
    block_type: str
    ordinal: int
    text: str
    section_path: str
    page_start: int | None = None
    page_end: int | None = None
    bbox: tuple[float, ...] | None = None
    source_parser: str = ""
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PaperDocument:
    document_id: str
    work_id: str
    source: SourceInfo
    bibliographic_metadata: Mapping[str, object]
    blocks: tuple[PaperBlock, ...]
    relations: tuple[Mapping[str, object], ...]
    normalized_markdown: str
    diagnostics: Mapping[str, object]
    schema_version: str = PAPER_DOCUMENT_SCHEMA_VERSION

    def to_record(self) -> dict[str, Any]:
        value = asdict(self)
        value["normalized_markdown_sha256"] = _sha256_text(
            self.normalized_markdown
        )
        value["block_ids_sha256"] = hashlib.sha256(
            "\n".join(block.block_id for block in self.blocks).encode("utf-8")
        ).hexdigest()
        return value


def _first_heading(markdown: str) -> str | None:
    for line in markdown.splitlines():
        match = _HEADING_RE.match(line.strip())
        if match:
            return match.group("title").strip()
    return None


def _abstract(markdown: str) -> str | None:
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        match = _HEADING_RE.match(line.strip())
        if not match or _compact(match.group("title")) not in {"abstract", "摘要"}:
            continue
        body = []
        for candidate in lines[index + 1:]:
            if _HEADING_RE.match(candidate.strip()):
                break
            if candidate.strip():
                body.append(candidate.strip())
        value = " ".join(body).strip()
        return value or None
    return None


def _normalize_authors(value: object) -> list[str]:
    if isinstance(value, str):
        values = re.split(r"\s*(?:,|;|；|，|\band\b)\s*", value)
    elif isinstance(value, Sequence):
        values = [str(item) for item in value]
    else:
        values = []
    return [item.strip() for item in values if item.strip()]


def merge_bibliographic_metadata(
    parser_metadata: Mapping[str, object] | None,
    grobid_metadata: Mapping[str, object] | None,
    catalog_metadata: Mapping[str, object] | None,
    *,
    markdown: str,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """按 parsed -> GROBID -> catalog 补空，不用冲突值静默覆盖。"""

    base = {
        "title": _first_heading(markdown),
        "authors": [],
        "affiliations": [],
        "publication_year": None,
        "venue": None,
        "language": None,
        "keywords": [],
        "abstract": _abstract(markdown),
        "doi": None,
        "arxiv_id": None,
        "external_ids": {},
        "references": [],
        "field_sources": {},
    }
    if base["title"]:
        base["field_sources"]["title"] = "markdown_heuristic"
    if base["abstract"]:
        base["field_sources"]["abstract"] = "markdown_heuristic"
    conflicts: list[dict[str, object]] = []
    for source_name, incoming in (
        ("docling", parser_metadata or {}),
        ("grobid", grobid_metadata or {}),
        ("catalog", catalog_metadata or {}),
    ):
        for field_name in (
            "title", "authors", "affiliations", "publication_year", "venue",
            "language", "keywords", "abstract", "doi", "arxiv_id",
            "external_ids", "references",
        ):
            candidate = incoming.get(field_name)
            if field_name == "authors":
                candidate = _normalize_authors(candidate)
            if candidate in (None, "", [], {}):
                continue
            current = base.get(field_name)
            if current in (None, "", [], {}):
                base[field_name] = candidate
                base["field_sources"][field_name] = source_name
            elif base["field_sources"].get(field_name) == "markdown_heuristic":
                if _compact(current) != _compact(candidate):
                    conflicts.append({
                        "field": field_name,
                        "kept_source": source_name,
                        "rejected_source": "markdown_heuristic",
                        "kept_value": candidate,
                        "rejected_value": current,
                    })
                base[field_name] = candidate
                base["field_sources"][field_name] = source_name
            elif _compact(current) != _compact(candidate):
                conflicts.append({
                    "field": field_name,
                    "kept_source": base["field_sources"].get(
                        field_name, "markdown_heuristic"
                    ),
                    "rejected_source": source_name,
                    "kept_value": current,
                    "rejected_value": candidate,
                })
    return base, conflicts


def markdown_blocks(
    markdown: str,
    document_id: str,
    *,
    source_parser: str,
) -> tuple[tuple[PaperBlock, ...], tuple[Mapping[str, object], ...]]:
    """构造阶段 1 的稳定结构块；父子检索节点留到阶段 2。"""

    heading_stack: list[tuple[int, str]] = []
    raw_blocks: list[tuple[str, str, str]] = []
    buffer: list[str] = []

    def section_path() -> str:
        return " > ".join(title for _, title in heading_stack) or "[root]"

    def flush() -> None:
        if not buffer:
            return
        text = "\n".join(buffer).strip()
        buffer.clear()
        if not text:
            return
        lines = [line for line in text.splitlines() if line.strip()]
        if any(line.lstrip().startswith("|") for line in lines):
            block_type = "table"
        elif text.startswith(("$$", "\\[", "\\begin{equation}")):
            block_type = "formula"
        elif _FIGURE_RE.match(lines[0].strip()):
            block_type = "figure_caption"
        elif _TABLE_RE.match(lines[0].strip()):
            block_type = "table_caption"
        elif heading_stack and _REFERENCE_HEADING_RE.match(heading_stack[-1][1]):
            block_type = "reference"
        else:
            block_type = "paragraph"
        raw_blocks.append((block_type, text, section_path()))

    for line in markdown.splitlines():
        match = _HEADING_RE.match(line.strip())
        if match:
            flush()
            level = len(match.group("marks"))
            title = match.group("title").strip()
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
            raw_blocks.append(("section", title, section_path()))
        elif not line.strip():
            flush()
        else:
            buffer.append(line)
    flush()

    # 某些双栏 PDF 的回退文本会把每个英文词拆成独立段落。阶段 1 在结构层
    # 合并同章节连续微片段，既保留原始 Markdown，又避免生成数千个伪段落。
    coalesced: list[tuple[str, str, str]] = []
    for block_type, text, path in raw_blocks:
        if (
            block_type == "paragraph"
            and coalesced
            and coalesced[-1][0] == "paragraph"
            and coalesced[-1][2] == path
            and (len(text) < 80 or len(coalesced[-1][1]) < 80)
            and len(coalesced[-1][1]) + len(text) + 1 <= 500
        ):
            previous_type, previous_text, previous_path = coalesced[-1]
            separator = "" if text in {".", ",", ";", ":", ")", "]"} else " "
            coalesced[-1] = (
                previous_type,
                f"{previous_text}{separator}{text}",
                previous_path,
            )
        else:
            coalesced.append((block_type, text, path))
    raw_blocks = coalesced

    blocks = []
    relations: list[Mapping[str, object]] = []
    previous_id: str | None = None
    section_ids: dict[str, str] = {}
    for ordinal, (block_type, text, path) in enumerate(raw_blocks):
        block_id = deterministic_paper_block_id(
            document_id, block_type, ordinal, text, section_path=path
        )
        block = PaperBlock(
            block_id=block_id,
            block_type=block_type,
            ordinal=ordinal,
            text=text,
            section_path=path,
            source_parser=source_parser,
        )
        blocks.append(block)
        if previous_id:
            relations.append({
                "relation_type": "next",
                "source_id": previous_id,
                "target_id": block_id,
            })
        previous_id = block_id
        if block_type == "section":
            section_ids[path] = block_id
        else:
            parent_id = section_ids.get(path)
            if parent_id:
                relations.append({
                    "relation_type": "section_parent",
                    "source_id": block_id,
                    "target_id": parent_id,
                })
    return tuple(blocks), tuple(relations)


class PaperDocumentNormalizer:
    def normalize(
        self,
        document: Document,
        *,
        source: SourceInfo,
        work_id: str = "",
        source_parser: str,
        parser_metadata: Mapping[str, object] | None = None,
        grobid_metadata: Mapping[str, object] | None = None,
        catalog_metadata: Mapping[str, object] | None = None,
        diagnostics: Mapping[str, object] | None = None,
    ) -> PaperDocument:
        markdown = document.text.strip()
        if not markdown:
            raise ValueError("PaperDocument 正文不能为空")
        document_id = deterministic_paper_document_id(source)
        bibliography, conflicts = merge_bibliographic_metadata(
            parser_metadata, grobid_metadata, catalog_metadata, markdown=markdown
        )
        blocks, relations = markdown_blocks(
            markdown, document_id, source_parser=source_parser
        )
        observed = dict(diagnostics or {})
        observed.update({
            "document_schema_version": PAPER_DOCUMENT_SCHEMA_VERSION,
            "ingestion_schema_version": PAPER_INGESTION_SCHEMA_VERSION,
            "block_count": len(blocks),
            "section_count": sum(b.block_type == "section" for b in blocks),
            "table_count": sum(b.block_type == "table" for b in blocks),
            "figure_count": sum(
                b.block_type == "figure_caption" for b in blocks
            ),
            "formula_count": sum(b.block_type == "formula" for b in blocks),
            "reference_count": sum(
                b.block_type == "reference" for b in blocks
            ),
            "metadata_conflicts": conflicts,
            "title_detected": bool(bibliography.get("title")),
            "abstract_detected": bool(bibliography.get("abstract")),
        })
        content_blocks = [block for block in blocks if block.block_type != "section"]
        micro_blocks = [block for block in content_blocks if len(block.text) < 20]
        page_count = observed.get("page_count")
        blocks_per_page = (
            round(len(content_blocks) / int(page_count), 2)
            if page_count else None
        )
        micro_ratio = round(
            len(micro_blocks) / len(content_blocks), 4
        ) if content_blocks else 0.0
        observed["blocks_per_page"] = blocks_per_page
        observed["micro_block_ratio"] = micro_ratio
        structural_warning = (
            (blocks_per_page is not None and blocks_per_page > 120)
            or micro_ratio > 0.80
        )
        if structural_warning:
            warnings = list(observed.get("warnings") or [])
            warnings.append("结构块异常碎片化，需要人工复核")
            observed["warnings"] = list(dict.fromkeys(warnings))
            observed["manual_review_required"] = True
            if observed.get("quality_status") != "blocked":
                observed["quality_status"] = "degraded"
        else:
            observed["manual_review_required"] = bool(
                observed.get("manual_review_required", False)
            )
        return PaperDocument(
            document_id=document_id,
            work_id=work_id,
            source=source,
            bibliographic_metadata=bibliography,
            blocks=blocks,
            relations=relations,
            normalized_markdown=markdown,
            diagnostics=observed,
        )


__all__ = [
    "PAPER_DOCUMENT_SCHEMA_VERSION",
    "PAPER_INGESTION_SCHEMA_VERSION",
    "PAPER_PARSER_PROFILE",
    "PaperBlock",
    "PaperDocument",
    "PaperDocumentNormalizer",
    "deterministic_paper_block_id",
    "deterministic_paper_document_id",
    "markdown_blocks",
    "merge_bibliographic_metadata",
]
