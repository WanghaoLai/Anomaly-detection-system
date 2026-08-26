"""文档加载、清洗、切分、Node 解析和不可变制品存储边界。"""

from .storage import (
    DOCUMENT_SCHEMA_VERSION,
    RELEASE_SCHEMA_VERSION,
    KnowledgeArtifactRepository,
    JsonPaperDocumentStore,
    deterministic_document_id,
    deterministic_node_id,
    sha256_bytes,
    utc_now_iso,
)
from .docling_loader import (
    DoclingLoadResult,
    DoclingPaperLoader,
    DoclingUnavailableError,
)
from .grobid import GrobidEnrichmentResult, GrobidMetadataEnricher, parse_grobid_tei
from .paper_ingestion import PaperCorpusCandidateBuilder
from .paper_model import (
    PAPER_DOCUMENT_SCHEMA_VERSION,
    PAPER_INGESTION_SCHEMA_VERSION,
    PAPER_PARSER_PROFILE,
    PaperBlock,
    PaperDocument,
    PaperDocumentNormalizer,
    deterministic_paper_block_id,
    deterministic_paper_document_id,
)
from .routing import PaperParseResult, ParserRouter, probe_pdf
from .pipeline import AsyncIngestionExecutor, DocumentIngestionPipeline
from .parsing import MarkdownNodeParser
from .loading import (
    DefaultDocumentPreprocessor,
    MarkItDownDocumentLoader,
    PaperDocumentPreprocessor,
    SUPPORTED_DOCUMENT_EXTENSIONS,
    preprocess_pdf_markdown,
)
from .splitting import (
    PARSER_SCHEMA_VERSION,
    approx_token_len,
    chunk_paragraphs,
    split_paragraphs_with_headings,
)

__all__ = [
    "AsyncIngestionExecutor",
    "DOCUMENT_SCHEMA_VERSION",
    "DefaultDocumentPreprocessor",
    "PaperDocumentPreprocessor",
    "DocumentIngestionPipeline",
    "KnowledgeArtifactRepository",
    "JsonPaperDocumentStore",
    "MarkdownNodeParser",
    "MarkItDownDocumentLoader",
    "DoclingLoadResult",
    "DoclingPaperLoader",
    "DoclingUnavailableError",
    "GrobidEnrichmentResult",
    "GrobidMetadataEnricher",
    "PAPER_DOCUMENT_SCHEMA_VERSION",
    "PAPER_INGESTION_SCHEMA_VERSION",
    "PAPER_PARSER_PROFILE",
    "PaperBlock",
    "PaperCorpusCandidateBuilder",
    "PaperDocument",
    "PaperDocumentNormalizer",
    "PaperParseResult",
    "ParserRouter",
    "PARSER_SCHEMA_VERSION",
    "RELEASE_SCHEMA_VERSION",
    "SUPPORTED_DOCUMENT_EXTENSIONS",
    "approx_token_len",
    "chunk_paragraphs",
    "deterministic_document_id",
    "deterministic_paper_block_id",
    "deterministic_paper_document_id",
    "deterministic_node_id",
    "preprocess_pdf_markdown",
    "probe_pdf",
    "parse_grobid_tei",
    "sha256_bytes",
    "split_paragraphs_with_headings",
    "utc_now_iso",
]
