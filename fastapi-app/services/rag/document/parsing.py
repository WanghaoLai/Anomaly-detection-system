"""LlamaIndex NodeParser 适配器。

本模块是 LlamaIndex SDK 与应用端口的唯一交界面：向内复用纯 Markdown
分块算法，向外实现框架无关的 ``NodeParser`` Protocol。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Sequence

from llama_index.core import Document as LlamaIndexDocument
from llama_index.core.node_parser import NodeParser as LlamaIndexNodeParser
from llama_index.core.schema import BaseNode, MetadataMode, NodeRelationship, TextNode
from llama_index.core.utils import get_tokenizer
from pydantic import Field

from ..core.contracts import Document, Node
from .splitting import (
    DEFAULT_MIN_RATIO,
    DEFAULT_TARGET_RATIO,
    PARSER_SCHEMA_VERSION,
    chunk_paragraphs,
    split_paragraphs_with_headings,
)


def _stable_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _llama_token_len(text: str) -> int:
    """使用 LlamaIndex 自带的离线 tokenizer cache，不依赖运行时网络。"""

    return len(get_tokenizer()(text)) if text else 0


def _document_identity(text: str, metadata: dict[str, Any]) -> str:
    explicit = metadata.get("document_id")
    if explicit:
        return str(explicit)
    return _stable_hash({
        "schema": PARSER_SCHEMA_VERSION,
        "source_sha256": metadata.get("source_sha256") or metadata.get("sha256"),
        "filename": metadata.get("source_filename") or metadata.get("filename"),
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    })[:32]


def _stable_node_id(document_id: str, record: dict[str, Any]) -> str:
    return _stable_hash({
        "schema": PARSER_SCHEMA_VERSION,
        "document_id": document_id,
        "char_start": int(record.get("start") or 0),
        "char_end": int(record.get("end") or 0),
        "section_path": record.get("section_path") or "[root]",
        "text_sha256": hashlib.sha256(
            str(record.get("content") or "").encode("utf-8")
        ).hexdigest(),
    })


class MarkdownNodeParser(LlamaIndexNodeParser):
    """LlamaIndex NodeParser 实现，同时适配 P0 领域端口。"""

    chunk_tokens: int = Field(gt=0)
    overlap_tokens: int = Field(ge=0)
    target_ratio: float = Field(default=DEFAULT_TARGET_RATIO, gt=0, le=1)
    min_ratio: float = Field(default=DEFAULT_MIN_RATIO, gt=0, le=1)

    def __init__(
        self,
        chunk_tokens: int,
        overlap_tokens: int,
        *,
        target_ratio: float = DEFAULT_TARGET_RATIO,
        min_ratio: float = DEFAULT_MIN_RATIO,
        include_metadata: bool = True,
        include_prev_next_rel: bool = True,
    ) -> None:
        super().__init__(
            chunk_tokens=int(chunk_tokens),
            overlap_tokens=int(overlap_tokens),
            target_ratio=float(target_ratio),
            min_ratio=float(min_ratio),
            include_metadata=include_metadata,
            include_prev_next_rel=include_prev_next_rel,
        )
        chunk_paragraphs(
            [],
            self.chunk_tokens,
            self.overlap_tokens,
            target_ratio=self.target_ratio,
            min_ratio=self.min_ratio,
            token_counter=_llama_token_len,
        )

    def _parse_nodes(
        self,
        nodes: Sequence[BaseNode],
        show_progress: bool = False,
        **kwargs: Any,
    ) -> list[BaseNode]:
        del show_progress, kwargs
        parsed: list[BaseNode] = []
        for source_node in nodes:
            text = source_node.get_content(metadata_mode=MetadataMode.NONE)
            source_metadata = dict(source_node.metadata or {})
            document_id = _document_identity(text, source_metadata)
            records = chunk_paragraphs(
                split_paragraphs_with_headings(text),
                self.chunk_tokens,
                self.overlap_tokens,
                target_ratio=self.target_ratio,
                min_ratio=self.min_ratio,
                token_counter=_llama_token_len,
            )
            for chunk_index, raw_record in enumerate(records):
                record = dict(raw_record)
                content = str(record.pop("content"))
                start = int(record.get("start") or 0)
                end = int(record.get("end") or start)
                line_start = text.count("\n", 0, start) + 1
                line_end = text.count("\n", 0, max(start, end - 1)) + 1
                metadata = {
                    **source_metadata,
                    **record,
                    "chunk_index": chunk_index,
                    "document_id": document_id,
                    "source_filename": str(
                        source_metadata.get("source_filename")
                        or source_metadata.get("filename")
                        or "unknown"
                    ),
                    "source_sha256": str(
                        source_metadata.get("source_sha256")
                        or source_metadata.get("sha256")
                        or ""
                    ),
                    "source_uri": str(
                        source_metadata.get("source_uri")
                        or source_metadata.get("storage_key")
                        or ""
                    ),
                    "section_path": record.get("section_path") or "[root]",
                    "char_start": start,
                    "char_end": end,
                    "line_start": line_start,
                    "line_end": line_end,
                    "position": f"chars:{start}-{end};lines:{line_start}-{line_end}",
                    "parser_schema_version": PARSER_SCHEMA_VERSION,
                    "llama_node_type": "TextNode",
                }
                metadata["citation_label"] = (
                    f"{metadata['source_filename']} · {metadata['section_path']} "
                    f"· L{line_start}-L{line_end}"
                )
                node_id = _stable_node_id(document_id, {**record, "content": content})
                excluded = list(metadata.keys())
                parsed.append(TextNode(
                    id_=node_id,
                    text=content,
                    metadata=metadata,
                    relationships={
                        NodeRelationship.SOURCE: source_node.as_related_node_info()
                    },
                    start_char_idx=start,
                    end_char_idx=end,
                    excluded_embed_metadata_keys=excluded,
                    excluded_llm_metadata_keys=excluded,
                ))
        return parsed

    def parse(self, document: Document) -> list[Node]:
        """把 LlamaIndex 类型收敛为稳定的应用层 Node 端口。"""

        metadata = dict(document.metadata)
        if document.document_id:
            metadata["document_id"] = document.document_id
        if document.source is not None:
            metadata.update({
                "source_filename": document.source.filename,
                "source_extension": document.source.extension,
                "source_media_type": document.source.media_type,
                "source_byte_size": document.source.byte_size,
                "source_sha256": document.source.sha256,
                "source_uri": document.source.storage_key or "",
                "source_uploaded_at": document.source.uploaded_at,
            })
        document_id = _document_identity(document.text, metadata)
        native_document = LlamaIndexDocument(
            id_=document_id,
            text=document.text,
            metadata=metadata,
        )
        native_nodes = self.get_nodes_from_documents([native_document])
        result: list[Node] = []
        for native_node in native_nodes:
            node_metadata = dict(native_node.metadata)
            if native_node.prev_node is not None:
                node_metadata["previous_node_id"] = native_node.prev_node.node_id
            if native_node.next_node is not None:
                node_metadata["next_node_id"] = native_node.next_node.node_id
            node_metadata["source_node_id"] = native_document.node_id
            result.append(Node(
                text=native_node.get_content(metadata_mode=MetadataMode.NONE),
                metadata=node_metadata,
                node_id=native_node.node_id,
            ))
        return result


__all__ = ["MarkdownNodeParser"]
