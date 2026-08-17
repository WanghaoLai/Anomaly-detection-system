"""知识库服务 - 文档解析、分块、向量化、ChromaDB 存储与检索"""
import hashlib
import logging
import math
import os
import re
import uuid
from collections import Counter
from typing import Dict, List, Optional, Tuple

import chromadb
from chromadb.config import Settings as ChromaSettings
from dashscope import TextEmbedding

from settings import AI_CONFIG
from .rag.contracts import Document
from .rag.embeddings import DashScopeEmbeddingModel
from .rag.loaders import (
    DefaultDocumentPreprocessor,
    MarkItDownDocumentLoader,
    SUPPORTED_DOCUMENT_EXTENSIONS as RAG_SUPPORTED_DOCUMENT_EXTENSIONS,
    preprocess_pdf_markdown as _rag_preprocess_pdf_markdown,
)
from .rag.splitters import (
    MarkdownNodeParser,
    approx_token_len as _rag_approx_token_len,
    chunk_paragraphs as _rag_chunk_paragraphs,
    split_paragraphs_with_headings as _rag_split_paragraphs_with_headings,
)
from .rag.vector_store import ChromaVectorStore

try:
    from markitdown import MarkItDown, StreamInfo
except ImportError:  # 依赖在部署环境中由 requirements.txt 提供；保留惰性报错便于非 RAG 测试启动。
    MarkItDown = None
    StreamInfo = None

logger = logging.getLogger(__name__)

CHROMA_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "chroma_db")

DOC_COLLECTION = "knowledge_base"

# 这是应用层的安全白名单，不等同于 MarkItDown 的全部能力。图片、音频、URL
# 等输入不作为知识库文档接收，避免把不必要的多模态/网络访问引入上传链路。
SUPPORTED_DOCUMENT_EXTENSIONS = frozenset({
    ".txt",
    ".md",
    ".markdown",
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".xls",
    ".csv",
    ".html",
    ".htm",
    ".json",
    ".xml",
    ".ipynb",
    ".epub",
})

# 对外常量继续由本模块导出；实际加载器使用 RAG 包中的同一契约。
assert SUPPORTED_DOCUMENT_EXTENSIONS == RAG_SUPPORTED_DOCUMENT_EXTENSIONS

# text-embedding-v2 按余弦相似度训练，Chroma 默认 hnsw:space=L2，二者向量空间语义
# 不对齐，召回质量会打折。这里显式指定 cosine，并把嵌入契约写入 collection，
# 让换模型、换归一化策略或混用旧索引时能够被检测出来，而不是静默污染召回结果。
EMBEDDING_PROVIDER = "dashscope"
EMBEDDING_SCHEMA_VERSION = "dashscope-text-embedding-v1"
# 原文件哈希相同不代表预处理结果相同。分块、PDF 清理等逻辑升级时提升
# 该版本，同名文件下次上传会重建一次，之后仍可正常命中重复上传。
INGESTION_SCHEMA_VERSION = "markitdown-pdf-cleanup-v1"
BASE_COLLECTION_METADATA = {
    "hnsw:space": "cosine",
    "embedding_provider": EMBEDDING_PROVIDER,
    "embedding_schema_version": EMBEDDING_SCHEMA_VERSION,
    "embedding_normalized": True,
}

_MARKDOWN_HEADING_RE = re.compile(
    r"^ {0,3}(?P<marks>#{1,6})(?:[ \t]+(?P<title>.*?)\s*|[ \t]*)$"
)
_MARKDOWN_FENCE_RE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})")
_PDF_PAGE_NUMBER_RE = re.compile(
    r"^\s*(?:第\s*\d+\s*页|page\s*\d+(?:\s*(?:/|of)\s*\d+)?)\s*$",
    re.IGNORECASE,
)
_NUMBERED_TITLE_RE = re.compile(
    r"^\s*(?P<number>\d+(?:\.\d+){0,4})[.、]?\s+(?P<title>\S.*?)\s*$"
)
_CHINESE_TITLE_RE = re.compile(
    r"^\s*(?:(?:第[一二三四五六七八九十百零〇\d]+[章节篇部])|(?:[一二三四五六七八九十]+、))\s*(?P<title>\S.*?)\s*$"
)


def _normalize_repeated_pdf_line(line: str) -> str:
    """规范化页边界文本，用于识别带页码变化的重复页眉页脚。"""
    normalized = re.sub(r"\s+", " ", line.strip()).lower()
    return re.sub(r"\d+", "#", normalized)


def _split_pdf_pages(markdown: str) -> Tuple[List[List[str]], int]:
    """利用换页符或独立页码行切分页，不依赖特定 PDF 解析器。"""
    pages: List[List[str]] = []
    current: List[str] = []
    page_markers = 0
    expanded = markdown.replace("\f", "\n\f\n")
    for line in expanded.splitlines():
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


def _page_boundary_indexes(lines: List[str], *, from_start: bool) -> List[int]:
    nonempty = [index for index, line in enumerate(lines) if line.strip()]
    selected = nonempty[:3] if from_start else nonempty[-3:]
    return selected


def _remove_repeated_pdf_boundaries(
    pages: List[List[str]],
) -> Tuple[List[List[str]], List[str], List[str]]:
    """只在每页前三/后三个非空行中清理高频文本，避免误删正文。"""
    if len(pages) < 3:
        return pages, [], []

    header_counter: Counter = Counter()
    footer_counter: Counter = Counter()
    for page in pages:
        for index in _page_boundary_indexes(page, from_start=True):
            line = page[index].strip()
            if 2 <= len(line) <= 120:
                key = _normalize_repeated_pdf_line(line)
                header_counter[key] += 1
        for index in _page_boundary_indexes(page, from_start=False):
            line = page[index].strip()
            if 2 <= len(line) <= 120:
                key = _normalize_repeated_pdf_line(line)
                footer_counter[key] += 1

    minimum_occurrences = max(3, math.ceil(len(pages) * 0.6))
    repeated_headers = {
        key for key, count in header_counter.items() if count >= minimum_occurrences
    }
    repeated_footers = {
        key for key, count in footer_counter.items() if count >= minimum_occurrences
    }

    cleaned_pages: List[List[str]] = []
    seen_headers = set()
    removed_headers: List[str] = []
    removed_footers: List[str] = []
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
                # 文档首页保留一次标题，后续页移除重复页眉。
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


def _recognize_pdf_titles(markdown: str) -> Tuple[str, List[str]]:
    """把常见章节编号转换为 Markdown 标题，供现有语义分块器使用。"""
    converted: List[str] = []
    detected: List[str] = []
    fence: Optional[Tuple[str, int]] = None
    for line in markdown.splitlines():
        fence_match = _MARKDOWN_FENCE_RE.match(line)
        if fence is not None:
            converted.append(line)
            if (
                fence_match
                and fence_match.group("fence")[0] == fence[0]
                and len(fence_match.group("fence")) >= fence[1]
            ):
                fence = None
            continue
        if fence_match:
            marker = fence_match.group("fence")
            fence = (marker[0], len(marker))
            converted.append(line)
            continue
        if _MARKDOWN_HEADING_RE.match(line):
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


def _preprocess_pdf_markdown(markdown: str) -> Tuple[str, Dict]:
    """执行保守的 PDF 清理并返回可供预览的诊断数据。"""
    pages, page_markers = _split_pdf_pages(markdown)
    pages, removed_headers, removed_footers = _remove_repeated_pdf_boundaries(pages)
    cleaned = "\n\n".join("\n".join(page).strip() for page in pages if any(line.strip() for line in page))
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


def _is_cjk(ch: str) -> bool:
    """判断字符是否属于 CJK 范围。"""
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0x20000 <= code <= 0x2A6DF
        or 0x2A700 <= code <= 0x2B73F
        or 0x2B740 <= code <= 0x2B81F
        or 0x2B820 <= code <= 0x2CEAF
        or 0xF900 <= code <= 0xFAFF
    )


def _approx_token_len(text: str) -> int:
    """近似估计中英文混合文本的 Token 数量。

    中文字符通常独立承担语义，按 1 Token 估算；非 CJK 文本按空白分词
    估算。该方法用于控制分块边界，不替代 embedding 服务的真实 tokenizer。
    """
    cjk = sum(1 for ch in text if _is_cjk(ch))
    non_cjk_tokens = len([token for token in text.split() if token])
    return cjk + non_cjk_tokens


def _heading_markdown(heading_stack: List[Tuple[int, str]]) -> str:
    """把当前标题路径还原为可放回分块正文的 Markdown 标题上下文。"""
    return "\n".join(
        f"{'#' * level} {title}".rstrip()
        for level, title in heading_stack
    )


def _split_paragraphs_with_headings(text: str) -> List[Dict]:
    """按 Markdown 标题和空行切分段落，并保留标题路径及字符位置。

    标题行本身不作为独立正文段落，但会以 ``heading_markdown`` 和
    ``heading_path`` 绑定到其后的段落。代码围栏中的 ``#`` 不会被识别为标题。
    """
    if not text or not text.strip():
        return []

    lines = text.splitlines(keepends=True)
    heading_stack: List[Tuple[int, str]] = []
    paragraphs: List[Dict] = []
    buf: List[str] = []
    buf_start: Optional[int] = None
    char_pos = 0
    fence: Optional[Tuple[str, int]] = None

    def flush_buf(end_pos: int) -> None:
        nonlocal buf, buf_start
        if not buf:
            return

        content = "\n".join(buf).strip()
        start = buf_start if buf_start is not None else max(0, end_pos - len(content))
        buf = []
        buf_start = None
        if not content:
            return

        current_heading_path = (
            " > ".join(title for _, title in heading_stack)
            if heading_stack
            else None
        )
        paragraphs.append({
            "content": content,
            "heading_path": current_heading_path,
            "heading_markdown": _heading_markdown(heading_stack),
            "start": max(0, start),
            "end": min(len(text), end_pos),
        })

    for line_with_ending in lines:
        raw = line_with_ending.rstrip("\r\n")
        line_end = char_pos + len(line_with_ending)

        fence_match = _MARKDOWN_FENCE_RE.match(raw)
        if fence is not None:
            if (
                fence_match
                and fence_match.group("fence")[0] == fence[0]
                and len(fence_match.group("fence")) >= fence[1]
            ):
                fence = None
            if buf_start is None:
                buf_start = char_pos
            buf.append(raw)
            char_pos = line_end
            continue

        if fence_match:
            if buf_start is None:
                buf_start = char_pos
            buf.append(raw)
            marker = fence_match.group("fence")
            fence = (marker[0], len(marker))
            char_pos = line_end
            continue

        heading_match = _MARKDOWN_HEADING_RE.match(raw)
        if heading_match:
            flush_buf(char_pos)
            level = len(heading_match.group("marks"))
            title = (heading_match.group("title") or "").strip()
            title = re.sub(r"[ \t]+#+[ \t]*$", "", title).strip()
            if level <= len(heading_stack):
                heading_stack = heading_stack[: level - 1]
            heading_stack.append((level, title))
            char_pos = line_end
            continue

        if raw.strip() == "":
            flush_buf(char_pos)
            char_pos = line_end
            continue

        if buf_start is None:
            buf_start = char_pos
        buf.append(raw)
        char_pos = line_end

    flush_buf(char_pos)

    # 只有标题没有正文时，保留原始 Markdown，避免丢失标题结构。
    if not paragraphs:
        content = text.strip()
        if not content:
            return []
        paragraphs = [{
            "content": content,
            "heading_path": None,
            "heading_markdown": "",
            "start": 0,
            "end": len(text),
        }]

    return paragraphs


def _paragraph_token_len(paragraph: Dict) -> int:
    heading = paragraph.get("heading_markdown") or ""
    content = paragraph.get("content") or ""
    rendered = f"{heading}\n\n{content}" if heading else content
    return _approx_token_len(rendered) or 1


def _contains_fenced_code(text: str) -> bool:
    """判断段落中是否包含 fenced code，避免为控长破坏代码围栏。"""
    return sum(1 for line in text.splitlines() if _MARKDOWN_FENCE_RE.match(line)) >= 2


def _split_text_by_token_budget(text: str, chunk_tokens: int) -> List[str]:
    """把超长普通段落按近似 Token 预算拆成最小可用文本片段。"""
    if _approx_token_len(text) <= chunk_tokens:
        return [text]

    # 单个 CJK 字符、非空白词和空白片段分别作为可拼接单元，尽量不在单词中间切断。
    units = re.findall(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]|[^\s]+|\s+", text)
    parts: List[str] = []
    current = ""

    for unit in units:
        candidate = current + unit
        if current.strip() and _approx_token_len(candidate) > chunk_tokens:
            parts.append(current.strip())
            current = unit.lstrip()
        else:
            current = candidate

    if current.strip():
        parts.append(current.strip())
    return parts or [text]


def _split_oversized_paragraph(paragraph: Dict, chunk_tokens: int) -> List[Dict]:
    """拆分超过 Token 预算的段落，同时保留标题上下文和大致字符位置。"""
    if _paragraph_token_len(paragraph) <= chunk_tokens:
        item = dict(paragraph)
        item["token_count"] = _paragraph_token_len(item)
        return [item]

    content = paragraph.get("content") or ""
    # 代码块是一个 Markdown 结构单元，宁可允许它暂时超长，也不生成缺失围栏的无效 Markdown。
    if _contains_fenced_code(content):
        item = dict(paragraph)
        item["token_count"] = _paragraph_token_len(item)
        return [item]

    heading_tokens = _approx_token_len(paragraph.get("heading_markdown") or "")
    body_budget = max(1, chunk_tokens - heading_tokens)
    parts = _split_text_by_token_budget(content, body_budget)
    result: List[Dict] = []
    search_from = 0
    for part in parts:
        relative_start = content.find(part, search_from)
        if relative_start < 0:
            relative_start = search_from
        relative_end = relative_start + len(part)
        search_from = relative_end

        item = dict(paragraph)
        item["content"] = part
        item["start"] = paragraph.get("start", 0) + relative_start
        item["end"] = paragraph.get("start", 0) + relative_end
        item["token_count"] = _paragraph_token_len(item)
        result.append(item)
    return result


def _render_chunk_markdown(paragraphs: List[Dict]) -> str:
    """渲染分块正文，在标题路径发生变化时补回 Markdown 标题上下文。"""
    rendered: List[str] = []
    active_heading = None
    for paragraph in paragraphs:
        heading = paragraph.get("heading_markdown") or ""
        if heading and heading != active_heading:
            rendered.append(heading)
            active_heading = heading
        content = (paragraph.get("content") or "").strip()
        if content:
            rendered.append(content)
    return "\n\n".join(rendered).strip()


def _chunk_paragraphs(
    paragraphs: List[Dict],
    chunk_tokens: int,
    overlap_tokens: int,
) -> List[Dict]:
    """按 Token 预算合并语义段落，并以完整段落为单位构建重叠。"""
    if chunk_tokens <= 0:
        raise ValueError("chunk_tokens 必须大于 0")
    if overlap_tokens < 0 or overlap_tokens >= chunk_tokens:
        raise ValueError("overlap_tokens 必须满足 0 <= overlap_tokens < chunk_tokens")

    normalized: List[Dict] = []
    for paragraph in paragraphs:
        normalized.extend(_split_oversized_paragraph(paragraph, chunk_tokens))

    chunks: List[Dict] = []
    start_index = 0
    while start_index < len(normalized):
        current: List[Dict] = []
        current_tokens = 0
        end_index = start_index

        while end_index < len(normalized):
            paragraph = normalized[end_index]
            paragraph_tokens = paragraph.get("token_count") or _paragraph_token_len(paragraph)
            if current and current_tokens + paragraph_tokens > chunk_tokens:
                break
            current.append(paragraph)
            current_tokens += paragraph_tokens
            end_index += 1

        content = _render_chunk_markdown(current)
        heading_paths = []
        for item in current:
            path = item.get("heading_path")
            if path and path not in heading_paths:
                heading_paths.append(path)
        heading_path = heading_paths[0] if heading_paths else None
        chunks.append({
            "content": content,
            "start": current[0].get("start", 0),
            "end": current[-1].get("end", 0),
            "heading_path": heading_path,
            "heading_paths": " | ".join(heading_paths) if heading_paths else None,
            "token_count": _approx_token_len(content) or 1,
            "paragraph_count": len(current),
        })

        if end_index >= len(normalized):
            break

        # 从当前分块尾部回溯完整段落，保证 overlap 不切断 Markdown 结构。
        next_start = end_index
        kept_tokens = 0
        while next_start > start_index:
            candidate = normalized[next_start - 1]
            candidate_tokens = candidate.get("token_count") or _paragraph_token_len(candidate)
            if kept_tokens + candidate_tokens > overlap_tokens:
                break
            next_start -= 1
            kept_tokens += candidate_tokens

        # 只含一个很短段落时，不能重复同一段导致死循环；下一轮必须向前推进。
        start_index = max(start_index + 1, next_start)

    return chunks


# 旧模块路径仍对测试、评测脚本开放，但实现统一指向新的核心组件，避免两套逻辑漂移。
_preprocess_pdf_markdown = _rag_preprocess_pdf_markdown
_approx_token_len = _rag_approx_token_len
_split_paragraphs_with_headings = _rag_split_paragraphs_with_headings
_chunk_paragraphs = _rag_chunk_paragraphs


class KnowledgeService:
    def __init__(
        self,
        embedding_model: str = None,
        markdown_converter=None,
        chunk_tokens: int = None,
        overlap_tokens: int = None,
        embedding_batch_size: int = None,
        embedding_max_retries: int = None,
        embedding_retry_backoff_seconds: float = None,
        document_loader=None,
        document_preprocessor=None,
        node_parser=None,
        embedding=None,
        vector_store=None,
    ):
        self.embedding_model = embedding_model or AI_CONFIG.get("embedding_model", "text-embedding-v2")
        self.dashscope_api_key = AI_CONFIG.get("dashscope_api_key", "")
        self._client = None
        self._collection = None
        self._markdown_converter = markdown_converter
        self._probed_dim = None  # 首次 _get_embeddings 后填充，便于校验
        # 新向量写入成功、SQL 元数据尚未提交期间保留旧文档快照。以新 doc_id
        # 为键，避免并发上传时一个请求覆盖另一个请求的回滚信息。
        self._replacement_snapshots: Dict[str, Dict] = {}
        self.chunk_tokens = int(
            chunk_tokens
            if chunk_tokens is not None
            else AI_CONFIG.get("rag_chunk_tokens", 500)
        )
        self.overlap_tokens = int(
            overlap_tokens
            if overlap_tokens is not None
            else AI_CONFIG.get("rag_overlap_tokens", 50)
        )
        self.embedding_batch_size = int(
            embedding_batch_size
            if embedding_batch_size is not None
            else AI_CONFIG.get("embedding_batch_size", 25)
        )
        self.embedding_max_retries = int(
            embedding_max_retries
            if embedding_max_retries is not None
            else AI_CONFIG.get("embedding_max_retries", 3)
        )
        self.embedding_retry_backoff_seconds = float(
            embedding_retry_backoff_seconds
            if embedding_retry_backoff_seconds is not None
            else AI_CONFIG.get("embedding_retry_backoff_seconds", 0.5)
        )
        if self.chunk_tokens <= 0:
            raise ValueError("chunk_tokens 必须大于 0")
        if self.overlap_tokens < 0 or self.overlap_tokens >= self.chunk_tokens:
            raise ValueError("overlap_tokens 必须满足 0 <= overlap_tokens < chunk_tokens")
        if self.embedding_batch_size <= 0:
            raise ValueError("embedding_batch_size 必须大于 0")
        if self.embedding_max_retries < 0:
            raise ValueError("embedding_max_retries 不能小于 0")
        if self.embedding_retry_backoff_seconds < 0:
            raise ValueError("embedding_retry_backoff_seconds 不能小于 0")

        # RAG 各阶段均为可替换组件；KnowledgeService 只保留兼容门面和跨存储事务。
        self.document_loader = document_loader or MarkItDownDocumentLoader(
            lambda: self.markdown_converter,
            StreamInfo,
        )
        self.document_preprocessor = document_preprocessor or DefaultDocumentPreprocessor()
        self.node_parser = node_parser or MarkdownNodeParser(
            self.chunk_tokens, self.overlap_tokens
        )
        self.embedding = embedding or DashScopeEmbeddingModel(
            api=TextEmbedding,
            model=self.embedding_model,
            api_key=self.dashscope_api_key,
            batch_size=self.embedding_batch_size,
            max_retries=self.embedding_max_retries,
            retry_backoff_seconds=self.embedding_retry_backoff_seconds,
        )
        self.vector_store = vector_store or ChromaVectorStore(lambda: self.collection)

    @property
    def client(self):
        if self._client is None:
            os.makedirs(CHROMA_PATH, exist_ok=True)
            self._client = chromadb.PersistentClient(
                path=CHROMA_PATH, settings=ChromaSettings(anonymized_telemetry=False)
            )
        return self._client

    @property
    def collection(self):
        if self._collection is None:
            self._collection = self.client.get_or_create_collection(
                name=DOC_COLLECTION,
                # collection metadata 创建后不可直接改，这里把模型和嵌入契约一起写入；
                # 维度在首次成功调用 embedding 后再补入。
                metadata={**BASE_COLLECTION_METADATA, "embedding_model": self.embedding_model},
            )
        return self._collection

    # ==================== 文档知识库 ====================

    @property
    def markdown_converter(self):
        """返回统一的 MarkItDown 转换器实例。"""
        if self._markdown_converter is None:
            if MarkItDown is None:
                raise RuntimeError(
                    "MarkItDown 依赖未安装，请执行 pip install -r requirements.txt"
                )
            # 上传链路只传入内存字节流，关闭插件以避免加载未审核的第三方转换器。
            self._markdown_converter = MarkItDown(enable_plugins=False)
        return self._markdown_converter

    @staticmethod
    def _safe_filename(filename: str) -> str:
        return os.path.basename((filename or "").replace("\\", "/"))

    def convert_to_markdown(self, file_bytes: bytes, filename: str) -> str:
        """使用 MarkItDown 将上传文件转换为 Markdown。

        这是进入 RAG 后续流程的唯一文档入口：调用方只能把本方法返回的
        Markdown 交给分块、向量化和 Chroma 存储，避免不同文件格式走不同解析逻辑。
        使用 convert_stream 而不是 convert，确保用户提供的文件名不会被当作本地路径
        或 URL 访问。
        """
        return self.document_loader.load(file_bytes, filename).text

    def parse_file(self, file_bytes: bytes, filename: str) -> str:
        """兼容旧调用方；所有文件解析统一委托给 MarkItDown。"""
        return self.convert_to_markdown(file_bytes, filename)

    def prepare_document(self, file_bytes: bytes, filename: str) -> dict:
        """转换并清理文档，返回入库 Markdown、分块和质量诊断。"""
        source_filename = self._safe_filename(filename)
        extension = os.path.splitext(source_filename)[1].lower()
        raw_markdown = self.convert_to_markdown(file_bytes, source_filename)
        document, diagnostics = self.document_preprocessor.process(Document(
            text=raw_markdown,
            metadata={"filename": source_filename, "extension": extension},
        ))
        markdown = document.text
        chunks = self.split_markdown(markdown)
        if not chunks:
            raise ValueError("文档分块后无有效内容")
        diagnostics["chunk_count"] = len(chunks)
        diagnostics["average_chunk_tokens"] = round(
            sum(int(chunk.get("token_count") or 0) for chunk in chunks) / len(chunks),
            1,
        )
        return {
            "filename": source_filename,
            "extension": extension,
            "markdown": markdown,
            "chunks": chunks,
            "diagnostics": diagnostics,
        }

    def preview_document(self, file_bytes: bytes, filename: str) -> dict:
        """只解析不入库，供管理员在 PDF 向量化前确认清理效果。"""
        prepared = self.prepare_document(file_bytes, filename)
        diagnostics = dict(prepared["diagnostics"])
        warnings = []
        if prepared["extension"] == ".pdf":
            if diagnostics["cleaned_char_count"] < 200:
                warnings.append("解析出的文字较少，可能是扫描 PDF，建议先执行 OCR")
            if diagnostics["detected_title_count"] == 0:
                warnings.append("未识别到章节标题，将主要按段落和长度分块")
            if diagnostics["page_count"] <= 1 and diagnostics["page_markers_removed"] == 0:
                warnings.append("未识别到明确分页，无法可靠判断重复页眉页脚")
        return {
            "filename": prepared["filename"],
            "extension": prepared["extension"],
            "diagnostics": diagnostics,
            "warnings": warnings,
            "preview_markdown": prepared["markdown"][:8000],
            "preview_truncated": len(prepared["markdown"]) > 8000,
            "chunk_previews": [
                {
                    "index": index,
                    "heading_path": chunk.get("heading_path"),
                    "token_count": chunk.get("token_count"),
                    "content": str(chunk.get("content") or "")[:1500],
                }
                for index, chunk in enumerate(prepared["chunks"][:3])
            ],
        }

    def split_markdown(self, markdown: str) -> List[Dict]:
        """执行 Markdown 标题分段和 Token 预算分块。"""
        nodes = self.node_parser.parse(Document(text=markdown))
        return [{"content": node.text, **dict(node.metadata)} for node in nodes]

    def split_text(self, text: str) -> list:
        """兼容旧调用方，仅返回分块正文；正文仍来自 Markdown 分块器。"""
        return [chunk["content"] for chunk in self.split_markdown(text)]

    def _get_embeddings(self, texts: list, *, text_type: str = "document") -> list:
        """兼容入口：委托给可替换的 EmbeddingModel 适配器。"""
        if text_type == "document":
            embeddings = self.embedding.embed_documents(texts)
        elif text_type == "query":
            embeddings = self.embedding.embed_queries(texts)
        else:
            raise ValueError("text_type 必须为 document 或 query")
        observed_dim = len(embeddings[0]) if embeddings else None
        if observed_dim is not None:
            if self._probed_dim is not None and self._probed_dim != observed_dim:
                raise RuntimeError(
                    f"Embedding 维度发生变化：expected={self._probed_dim}, actual={observed_dim}"
                )
            self._probed_dim = observed_dim
        return embeddings

    def add_document(self, file_bytes: bytes, filename: str) -> dict:
        source_filename = self._safe_filename(filename)
        extension = os.path.splitext(source_filename)[1].lower()
        if extension not in SUPPORTED_DOCUMENT_EXTENSIONS:
            raise ValueError(f"不支持的文件格式: {extension or '[无扩展名]'}")
        if not file_bytes:
            raise ValueError("文件内容为空")
        content_hash = hashlib.sha256(bytes(file_bytes)).hexdigest()

        prepared = self.prepare_document(file_bytes, source_filename)
        markdown = prepared["markdown"]
        if not markdown.strip():
            raise ValueError("文档内容为空，无法解析")

        chunk_records = prepared["chunks"]
        if not chunk_records:
            raise ValueError("文档分块后无有效内容")

        # 旧版本 collection 缺少新契约元数据时，重新上传同名文件应当是“替换该
        # 文档”，而不是让用户面对无法恢复的配置错误。只有确认 collection 中的
        # 全部 chunk 都来自同一个文件才自动重置；多文档旧库仍然强制阻断。
        replaced_existing = False
        replacement_snapshot = None
        report = self.validate_embedding_config()
        if not report["consistent"]:
            if self._collection_contains_only_filename(source_filename):
                replacement_snapshot = self._snapshot_and_reset_collection(source_filename)
                replaced_existing = True
                logger.warning(
                    "检测到同名旧知识库，已在上传前重建 collection: filename=%s",
                    source_filename,
                )
            else:
                self._ensure_consistent_or_raise("add_document")
        else:
            existing_snapshot = self._snapshot_filename_records(source_filename)
            if self._snapshot_is_same_content(existing_snapshot, content_hash):
                metadata = existing_snapshot["metadatas"][0]
                return {
                    "doc_id": metadata["doc_id"],
                    "chunk_count": len(existing_snapshot["ids"]),
                    "file_size": len(file_bytes),
                    "content_format": "markdown",
                    "content_hash": content_hash,
                    "replaced_existing": False,
                    "unchanged": True,
                    "previous_doc_ids": [metadata["doc_id"]],
                }
            if existing_snapshot:
                self._delete_snapshot_records(existing_snapshot)
                replacement_snapshot = existing_snapshot
                replaced_existing = True
            elif self._reset_empty_collection():
                logger.info("检测到已定型但为空的 collection，已重建向量 collection")

        chunks = [chunk["content"] for chunk in chunk_records]
        doc_uuid = None
        try:
            embeddings = self._get_embeddings(chunks)
            if len(embeddings) != len(chunks):
                raise RuntimeError(
                    f"Embedding 数量与 Markdown 分块数量不一致："
                    f"chunks={len(chunks)}, embeddings={len(embeddings)}"
                )
            dim = len(embeddings[0]) if embeddings else 0
            if dim <= 0 or any(len(vector) != dim for vector in embeddings):
                raise RuntimeError("Embedding 向量维度不一致，已拒绝写入知识库")

            # 维度在第一次成功生成向量后才确定。Chroma 的 HNSW 元数据在 collection
            # 创建后不可安全地增量修改，因此把维度写在每个 chunk 上，并在写入前/
            # 校验时严格核对。
            collection_metadata = dict(getattr(self.collection, "metadata", None) or {})
            existing_dim = collection_metadata.get("embedding_dimension")
            if existing_dim is not None:
                try:
                    collection_dim = int(existing_dim)
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(
                        f"向量库 collection.embedding_dimension 无效: {existing_dim!r}"
                    ) from exc
                if collection_dim != dim:
                    raise RuntimeError(
                        f"向量库维度不一致：collection={existing_dim}, actual={dim}"
                    )

            doc_uuid = uuid.uuid4().hex
            ids = [f"{doc_uuid}_{i}" for i in range(len(chunks))]
            metadatas = []
            for i, chunk in enumerate(chunk_records):
                metadata = {
                    "doc_id": doc_uuid,
                    "filename": source_filename,
                    "chunk_index": i,
                    "type": "document",
                    "content_format": "markdown",
                    "converter": "markitdown",
                    "source_extension": os.path.splitext(source_filename)[1].lower(),
                    "token_count": chunk["token_count"],
                    "char_start": chunk["start"],
                    "char_end": chunk["end"],
                    "paragraph_count": chunk["paragraph_count"],
                    "embedding_model": self.embedding_model,
                    "embedding_dim": dim,
                    "embedding_provider": EMBEDDING_PROVIDER,
                    "embedding_schema_version": EMBEDDING_SCHEMA_VERSION,
                    "embedding_normalized": True,
                    "embedding_text_type": "document",
                    "content_hash": content_hash,
                    "ingestion_schema_version": INGESTION_SCHEMA_VERSION,
                }
                if chunk.get("heading_path"):
                    metadata["heading_path"] = chunk["heading_path"]
                if chunk.get("heading_paths"):
                    metadata["heading_paths"] = chunk["heading_paths"]
                metadatas.append(metadata)

            try:
                self.vector_store.add(
                    ids=ids,
                    embeddings=embeddings,
                    documents=chunks,
                    metadatas=metadatas,
                )
            except Exception as exc:
                error_text = str(exc).lower()
                if "dimension" in error_text and "embedding" in error_text:
                    raise RuntimeError(
                        "Chroma 向量维度与当前嵌入模型不一致，已拒绝写入；"
                        "请重启后端后重试，空 collection 会自动重建"
                    ) from exc
                raise
        except Exception:
            if replacement_snapshot is not None:
                try:
                    if doc_uuid and replacement_snapshot.get("scope") != "collection":
                        self.delete_document(doc_uuid)
                    self._restore_replacement_snapshot(replacement_snapshot)
                except Exception as restore_exc:
                    logger.exception("旧知识库恢复失败: filename=%s", source_filename)
                    raise RuntimeError(
                        "新文档构建失败，且旧知识库恢复失败，请立即检查 Chroma 目录"
                    ) from restore_exc
            raise

        if replacement_snapshot is not None:
            self._replacement_snapshots[doc_uuid] = replacement_snapshot

        return {
            "doc_id": doc_uuid,
            "chunk_count": len(chunks),
            "file_size": len(file_bytes),
            "content_format": "markdown",
            "content_hash": content_hash,
            "replaced_existing": replaced_existing,
            "unchanged": False,
            "previous_doc_ids": self._snapshot_doc_ids(replacement_snapshot),
        }

    def count_document_chunks(self, doc_id: str) -> int:
        """只读统计指定文档的实际分块数。"""
        results = self.collection.get(where={"doc_id": doc_id}, include=[])
        return len(list(results.get("ids") or []))

    def snapshot_document(self, doc_id: str, expected_count: int = None) -> dict:
        """删除前建立文档快照，并在数量不符时保持索引不变。"""
        snapshot = self._snapshot_records(where={"doc_id": doc_id})
        actual_count = len(snapshot["ids"]) if snapshot else 0
        if expected_count is not None and actual_count != int(expected_count):
            raise ValueError(
                f"向量分块数量不一致：expected={expected_count}, actual={actual_count}"
            )
        return snapshot or self._empty_record_snapshot()

    def restore_document_snapshot(self, snapshot: dict) -> None:
        """在 SQL 删除失败时恢复已经删除的 Chroma 文档。"""
        self._restore_record_snapshot(snapshot)

    def delete_document(self, doc_id: str, expected_count: int = None) -> int:
        """预检数量后删除文档分块，并返回实际删除数量。"""
        results = self.collection.get(where={"doc_id": doc_id}, include=[])
        ids = list(results.get("ids") or [])
        if expected_count is not None and len(ids) != int(expected_count):
            raise ValueError(
                f"向量分块数量不一致：expected={expected_count}, actual={len(ids)}"
            )
        if ids:
            self.collection.delete(ids=ids)
        return len(ids)

    @staticmethod
    def _empty_record_snapshot() -> dict:
        return {
            "scope": "records",
            "ids": [],
            "embeddings": None,
            "documents": [],
            "metadatas": [],
        }

    def _snapshot_records(self, *, where: dict) -> Optional[dict]:
        """读取一组 Chroma 记录的完整快照，但不修改索引。"""
        col = self.collection
        if not hasattr(col, "get"):
            return None
        data = col.get(
            where=where,
            include=["embeddings", "documents", "metadatas"],
        )
        ids = list(data.get("ids") or [])
        if not ids:
            return None
        return {
            "scope": "records",
            "ids": ids,
            "embeddings": data.get("embeddings"),
            "documents": list(data.get("documents") or []),
            "metadatas": list(data.get("metadatas") or []),
        }

    def _snapshot_filename_records(self, source_filename: str) -> Optional[dict]:
        return self._snapshot_records(where={"filename": source_filename})

    @staticmethod
    def _snapshot_doc_ids(snapshot: Optional[dict]) -> List[str]:
        if not snapshot:
            return []
        doc_ids = []
        for metadata in snapshot.get("metadatas") or []:
            doc_id = (metadata or {}).get("doc_id")
            if doc_id and doc_id not in doc_ids:
                doc_ids.append(doc_id)
        return doc_ids

    @classmethod
    def _snapshot_is_same_content(cls, snapshot: Optional[dict], content_hash: str) -> bool:
        """仅在哈希与入库管线版本都一致时判定为重复上传。"""
        if not snapshot or not snapshot.get("ids"):
            return False
        metadatas = snapshot.get("metadatas") or []
        doc_ids = cls._snapshot_doc_ids(snapshot)
        hashes = {(metadata or {}).get("content_hash") for metadata in metadatas}
        ingestion_versions = {
            (metadata or {}).get("ingestion_schema_version") for metadata in metadatas
        }
        return (
            len(metadatas) == len(snapshot["ids"])
            and len(doc_ids) == 1
            and hashes == {content_hash}
            and ingestion_versions == {INGESTION_SCHEMA_VERSION}
        )

    def _delete_snapshot_records(self, snapshot: dict) -> None:
        ids = list(snapshot.get("ids") or [])
        if ids:
            self.collection.delete(ids=ids)

    def _restore_record_snapshot(self, snapshot: dict) -> None:
        ids = list(snapshot.get("ids") or [])
        if not ids:
            return
        embeddings = snapshot.get("embeddings")
        documents = list(snapshot.get("documents") or [])
        metadatas = list(snapshot.get("metadatas") or [])
        for start in range(0, len(ids), 100):
            end = start + 100
            batch_embeddings = embeddings[start:end] if embeddings is not None else None
            if hasattr(batch_embeddings, "tolist"):
                batch_embeddings = batch_embeddings.tolist()
            self.collection.add(
                ids=ids[start:end],
                embeddings=batch_embeddings,
                documents=documents[start:end],
                metadatas=metadatas[start:end],
            )

    def _restore_replacement_snapshot(self, snapshot: dict) -> None:
        if snapshot.get("scope") == "collection":
            self._restore_collection_snapshot(snapshot)
        else:
            self._restore_record_snapshot(snapshot)

    def _collection_contains_only_filename(self, source_filename: str) -> bool:
        """判断旧 collection 是否只包含本次要重新上传的同一个文件。"""
        names = [c.name for c in self.client.list_collections()]
        if DOC_COLLECTION not in names:
            return False

        col = self.client.get_collection(name=DOC_COLLECTION)
        count = col.count()
        if count == 0:
            return True

        data = col.get(include=["metadatas"])
        metadatas = list(data.get("metadatas") or [])
        if len(metadatas) != count:
            return False
        return all(
            isinstance(metadata, dict)
            and metadata.get("filename") == source_filename
            for metadata in metadatas
        )

    def _reset_empty_collection(self) -> bool:
        """重建空 collection，清除 Chroma 已经推断出的旧向量维度。

        Chroma 会在第一次写入时固定 collection 的 embedding dimension；删除全部
        chunk 不会解除这个约束。因此一个看似为空、但曾写入过 2 维测试向量的
        collection 仍然不能接收生产模型的 1536 维向量，只能删除并重新创建。
        """
        # 测试替身可能通过 _collection 注入而没有完整的 Chroma API；真实 collection
        # 都有 count 方法，避免兼容测试链路时误触碰工作区持久化库。
        if self._collection is not None and not hasattr(self._collection, "count"):
            return False

        names = [c.name for c in self.client.list_collections()]
        if DOC_COLLECTION not in names:
            return False
        existing = self.client.get_collection(name=DOC_COLLECTION)
        if existing.count() != 0:
            return False

        self.client.delete_collection(name=DOC_COLLECTION)
        self._collection = None
        self._probed_dim = None
        self.client.get_or_create_collection(
            name=DOC_COLLECTION,
            metadata={**BASE_COLLECTION_METADATA, "embedding_model": self.embedding_model},
        )
        return True

    def _snapshot_and_reset_collection(self, source_filename: str) -> dict:
        """为同名旧文档建立内存快照后重建空 collection。

        只有 ``_collection_contains_only_filename`` 已确认没有其他文件时才允许
        调用。快照用于新文件转换/嵌入失败时恢复旧索引，避免上传失败造成数据丢失。
        """
        if not self._collection_contains_only_filename(source_filename):
            raise RuntimeError(
                "旧知识库包含其他文档，不能自动清空；请先删除或完整重建知识库"
            )

        old = self.client.get_collection(name=DOC_COLLECTION)
        data = old.get(include=["embeddings", "documents", "metadatas"])
        snapshot = {
            "scope": "collection",
            "metadata": dict(old.metadata or {}),
            "ids": list(data.get("ids") or []),
            "embeddings": data.get("embeddings"),
            "documents": list(data.get("documents") or []),
            "metadatas": list(data.get("metadatas") or []),
        }
        self.client.delete_collection(name=DOC_COLLECTION)
        self._collection = None
        self._probed_dim = None
        return snapshot

    def _restore_collection_snapshot(self, snapshot: dict) -> None:
        """恢复 ``_snapshot_and_reset_collection`` 产生的旧 collection。"""
        names = [c.name for c in self.client.list_collections()]
        if DOC_COLLECTION in names:
            self.client.delete_collection(name=DOC_COLLECTION)

        restored = self.client.get_or_create_collection(
            name=DOC_COLLECTION,
            metadata=snapshot["metadata"],
        )
        ids = snapshot.get("ids") or []
        embeddings = snapshot.get("embeddings")
        documents = snapshot.get("documents") or []
        metadatas = snapshot.get("metadatas") or []
        if ids:
            for start in range(0, len(ids), 100):
                end = start + 100
                batch_embeddings = embeddings[start:end] if embeddings is not None else None
                if hasattr(batch_embeddings, "tolist"):
                    batch_embeddings = batch_embeddings.tolist()
                restored.add(
                    ids=ids[start:end],
                    embeddings=batch_embeddings,
                    documents=documents[start:end],
                    metadatas=metadatas[start:end],
                )
        self._collection = restored
        if embeddings is not None and len(embeddings) > 0:
            self._probed_dim = len(embeddings[0])

    def rollback_replacement(self, doc_id: str) -> bool:
        """在 SQL 元数据提交失败时恢复最近一次同名替换。"""
        snapshot = self._replacement_snapshots.pop(doc_id, None)
        if snapshot is None:
            return False
        # 新版本可能已经写入；先移除新 doc_id，再恢复旧版本。collection 级快照
        # 会整体重建，因此无需单独删除。
        if snapshot.get("scope") != "collection":
            self.delete_document(doc_id)
        self._restore_replacement_snapshot(snapshot)
        return True

    def complete_replacement(self, doc_id: str) -> None:
        """SQL 元数据提交成功后释放最近一次替换的内存快照。"""
        self._replacement_snapshots.pop(doc_id, None)

    def migrate_to_cosine(self, *, force: bool = False, keep_backup: bool = False) -> dict:
        """一次性迁移：把 collection 重建为 cosine 距离度量。

        Chroma 的 collection metadata 创建后不可修改，迁移必须读出全部 chunk +
        已有 embedding + metadata，删掉旧 collection，按 cosine 重建后再写回。
        复用已有 embeddings，不会重新调用 DashScope。

        幂等：检测到已是 cosine 直接返回，未改动。
        """
        existing_names = [c.name for c in self.client.list_collections()]
        if DOC_COLLECTION not in existing_names:
            meta = {**BASE_COLLECTION_METADATA, "embedding_model": self.embedding_model}
            self._collection = self.client.get_or_create_collection(
                name=DOC_COLLECTION, metadata=meta
            )
            return {
                "migrated": False,
                "reason": "no_existing_collection",
                "chunks": 0,
                "new_metadata": meta,
            }

        old = self.client.get_collection(name=DOC_COLLECTION)
        previous_metadata = old.metadata
        if (previous_metadata or {}).get("hnsw:space") == "cosine" and not force:
            self._collection = old
            return {
                "migrated": False,
                "reason": "already_cosine",
                "chunks": old.count(),
                "metadata": previous_metadata,
            }

        data = old.get(include=["embeddings", "documents", "metadatas"])
        ids = list(data.get("ids") or [])
        embeddings = data.get("embeddings")
        documents = list(data.get("documents") or [])
        metadatas = list(data.get("metadatas") or [])
        total = len(ids)

        backup_file = None
        if keep_backup and total > 0:
            import json
            backup_file = os.path.join(
                os.path.dirname(CHROMA_PATH),
                f"knowledge_base_backup_{uuid.uuid4().hex[:8]}.json",
            )
            emb_serializable = (
                embeddings.tolist() if hasattr(embeddings, "tolist") else list(embeddings)
            )
            with open(backup_file, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "ids": ids,
                        "embeddings": emb_serializable,
                        "documents": documents,
                        "metadatas": metadatas,
                    },
                    f,
                    ensure_ascii=False,
                )

        # 删除旧 collection，按 cosine 重建
        self.client.delete_collection(name=DOC_COLLECTION)
        new_meta = {**BASE_COLLECTION_METADATA, "embedding_model": self.embedding_model}
        new_col = self.client.get_or_create_collection(
            name=DOC_COLLECTION, metadata=new_meta
        )

        # 写回（已有 embeddings，无需重新向 DashScope 请求）
        if total > 0:
            batch_size = 100
            for i in range(0, total, batch_size):
                end = i + batch_size
                batch_emb = embeddings[i:end]
                new_col.add(
                    ids=ids[i:end],
                    embeddings=batch_emb.tolist() if hasattr(batch_emb, "tolist") else batch_emb,
                    documents=documents[i:end],
                    metadatas=metadatas[i:end],
                )

        self._collection = new_col
        return {
            "migrated": True,
            "reason": "rebuilt",
            "chunks": total,
            "previous_metadata": previous_metadata,
            "new_metadata": new_col.metadata,
            "backup_file": backup_file,
        }

    # ==================== 配置一致性校验 ====================

    def validate_embedding_config(self) -> dict:
        """校验 collection/chunk 元数据与当前 AI_CONFIG.embedding_model 是否一致。

        不会调用 DashScope，仅读 Chroma 本地数据。
        """
        names = [c.name for c in self.client.list_collections()]
        result = {
            "consistent": True,
            "issues": [],
            "expected_model": self.embedding_model,
            "collection_model": None,
            "collection_provider": None,
            "collection_schema_version": None,
            "collection_normalized": None,
            "collection_dim": None,
            "chunk_model": None,
            "chunk_provider": None,
            "chunk_schema_version": None,
            "chunk_normalized": None,
            "actual_dim": None,
            "chunk_dim": None,
        }
        if DOC_COLLECTION not in names:
            return result

        col = self.client.get_collection(name=DOC_COLLECTION)
        col_meta = col.metadata or {}
        result["collection_model"] = col_meta.get("embedding_model")
        result["collection_provider"] = col_meta.get("embedding_provider")
        result["collection_schema_version"] = col_meta.get("embedding_schema_version")
        result["collection_normalized"] = col_meta.get("embedding_normalized")
        result["collection_dim"] = col_meta.get("embedding_dimension")

        sample = col.peek(limit=1)
        sample_meta = (sample.get("metadatas") or [None])[0] or {}
        result["chunk_model"] = sample_meta.get("embedding_model")
        result["chunk_provider"] = sample_meta.get("embedding_provider")
        result["chunk_schema_version"] = sample_meta.get("embedding_schema_version")
        result["chunk_normalized"] = sample_meta.get("embedding_normalized")
        result["chunk_dim"] = sample_meta.get("embedding_dim")
        sample_emb = sample.get("embeddings")
        if sample_emb is not None and len(sample_emb) > 0:
            result["actual_dim"] = len(sample_emb[0])

        # collection 级契约：这些字段缺失也视为不一致。旧索引不能在未重建的
        # 情况下混入新向量，否则无法证明距离度量、归一化和模型相同。
        if col_meta.get("hnsw:space") != "cosine":
            result["issues"].append(
                f"collection.metadata.hnsw:space={col_meta.get('hnsw:space')!r} != 'cosine'"
            )
        if result["collection_model"] != self.embedding_model:
            result["issues"].append(
                f"collection.metadata.embedding_model={result['collection_model']!r} "
                f"!= config={self.embedding_model!r}"
            )
        if result["collection_provider"] != EMBEDDING_PROVIDER:
            result["issues"].append(
                "collection.metadata.embedding_provider="
                f"{result['collection_provider']!r} != {EMBEDDING_PROVIDER!r}"
            )
        if result["collection_schema_version"] != EMBEDDING_SCHEMA_VERSION:
            result["issues"].append(
                "collection.metadata.embedding_schema_version="
                f"{result['collection_schema_version']!r} != {EMBEDDING_SCHEMA_VERSION!r}"
            )
        if result["collection_normalized"] is not True:
            result["issues"].append(
                "collection.metadata.embedding_normalized 必须为 True"
            )

        # chunk 级（核心校验）
        if col.count() > 0 and result["chunk_model"] != self.embedding_model:
            result["issues"].append(
                f"chunk.embedding_model={result['chunk_model']!r} "
                f"!= config={self.embedding_model!r}"
            )
        if col.count() > 0 and result["chunk_provider"] != EMBEDDING_PROVIDER:
            result["issues"].append(
                "chunk.embedding_provider="
                f"{result['chunk_provider']!r} != {EMBEDDING_PROVIDER!r}"
            )
        if col.count() > 0 and result["chunk_schema_version"] != EMBEDDING_SCHEMA_VERSION:
            result["issues"].append(
                "chunk.embedding_schema_version="
                f"{result['chunk_schema_version']!r} != {EMBEDDING_SCHEMA_VERSION!r}"
            )
        if col.count() > 0 and result["chunk_normalized"] is not True:
            result["issues"].append("chunk.embedding_normalized 必须为 True")
        if result["chunk_dim"] and result["actual_dim"] and result["chunk_dim"] != result["actual_dim"]:
            result["issues"].append(
                f"chunk.embedding_dim={result['chunk_dim']} != actual={result['actual_dim']}"
            )
        if result["collection_dim"] is not None and result["actual_dim"] is not None:
            try:
                collection_dim = int(result["collection_dim"])
            except (TypeError, ValueError):
                collection_dim = None
            if collection_dim is None or collection_dim != result["actual_dim"]:
                result["issues"].append(
                    f"collection.embedding_dimension={result['collection_dim']!r} "
                    f"!= actual={result['actual_dim']}"
                )
        if col.count() > 0 and not result["chunk_model"]:
            result["issues"].append(
                "既有 chunk 未记录 embedding_model，无法判断模型是否一致；请重建知识库"
            )

        result["consistent"] = not result["issues"]
        return result

    def _ensure_consistent_or_raise(self, op: str):
        """add 路径强校验：配置不一致直接抛错，避免新旧 chunk 落入不同向量空间。"""
        report = self.validate_embedding_config()
        if not report["consistent"]:
            raise RuntimeError(
                f"embedding 配置不一致，已拒绝 {op}：\n  " +
                "\n  ".join(report["issues"]) +
                "\n请回滚 AI_CONFIG.embedding_model，或重新 embedding 全部历史数据。"
            )

    def _ensure_consistent_or_warn(self, op: str) -> bool:
        """search 路径软校验：不一致则记 error 并返回 False，让上层走兜底。"""
        report = self.validate_embedding_config()
        if not report["consistent"]:
            logger.error(
                "embedding 配置不一致，%s 已跳过 RAG 检索：\n  %s",
                op,
                "\n  ".join(report["issues"]),
            )
            return False
        return True

    def backfill_embedding_metadata(self, *, model: str = None) -> dict:
        """为已有 chunk 批量补写 embedding_model / embedding_dim 到 metadata。

        使用 collection.update 仅改 metadata，不重新调用 DashScope。
        前提：所有现有 chunk 都来自当前 AI_CONFIG.embedding_model（否则应走重嵌入迁移）。
        """
        model = model or self.embedding_model
        col = self.client.get_collection(name=DOC_COLLECTION)

        existing = col.get(include=["metadatas"])
        ids = list(existing.get("ids") or [])
        metadatas = list(existing.get("metadatas") or [])
        if not ids:
            return {"backfilled": False, "reason": "empty_collection",
                    "model": model, "chunks": 0}

        sample = col.peek(limit=1)
        sample_emb = sample.get("embeddings")
        if sample_emb is None or len(sample_emb) == 0:
            dim = None
        else:
            dim = len(sample_emb[0])

        batch_size = 100
        updated = 0
        for i in range(0, len(ids), batch_size):
            end = i + batch_size
            batch_ids = ids[i:end]
            new_meta = []
            for m in metadatas[i:end]:
                nm = dict(m or {})
                nm["embedding_model"] = model
                if dim is not None:
                    nm["embedding_dim"] = dim
                new_meta.append(nm)
            col.update(ids=batch_ids, metadatas=new_meta)
            updated += len(batch_ids)

        return {"backfilled": True, "chunks": updated, "model": model, "dim": dim}

    # ==================== 统一检索 ====================

    def _query_collection(self, col, query: str, top_k: int, source_label: str) -> list:
        if not isinstance(query, str) or not query.strip():
            return []
        try:
            requested_top_k = int(top_k)
        except (TypeError, ValueError):
            requested_top_k = 3
        if requested_top_k <= 0:
            return []

        collection_count = col.count()
        if collection_count <= 0:
            return []

        query_embedding = self._get_embeddings([query], text_type="query")[0]
        store = (
            self.vector_store
            if col is self.collection
            else ChromaVectorStore(lambda: col)
        )
        results = store.query(query_embedding, min(requested_top_k, collection_count))

        docs = []
        for result in results:
            meta = result["metadata"]
            docs.append({
                "content": result["content"],
                "source": source_label,
                "doc_id": meta.get("doc_id"),
                "filename": meta.get("filename", meta.get("name", "")),
                # Chroma 返回的是 cosine distance（越小越相关）；对外保留
                # score 作为 similarity，避免调用方把距离误判为相关性分数。
                "score": result["score"],
                "distance": result["distance"],
                "heading_path": meta.get("heading_path"),
                "heading_paths": meta.get(
                    "heading_paths", meta.get("heading_path")
                ),
                "chunk_index": meta.get("chunk_index"),
                "content_format": meta.get("content_format", "markdown"),
            })
        return docs

    def search_documents(self, query: str, top_k: int = 3) -> list:
        """仅检索文档知识库。配置不一致时记 error 并返回空，避免污染 LLM 上下文。"""
        if not self._ensure_consistent_or_warn("search_documents"):
            return []
        return self._query_collection(self.collection, query, top_k, "knowledge_base")

    def search(self, query: str, top_k: int = 3) -> list:
        """检索文档知识库"""
        return self.search_documents(query, top_k)

    def list_document_chunks(self) -> list:
        """读取现有 Chroma 分块，供轻量字面检索使用，不引入新索引。"""
        if not self._ensure_consistent_or_warn("list_document_chunks"):
            return []
        chunks = []
        for node in self.vector_store.list_nodes():
            content = node["content"]
            metadata = node["metadata"]
            chunks.append({
                "content": content,
                "source": "knowledge_base",
                "doc_id": metadata.get("doc_id"),
                "filename": metadata.get("filename", metadata.get("name", "")),
                "heading_path": metadata.get("heading_path"),
                "heading_paths": metadata.get(
                    "heading_paths",
                    metadata.get("heading_path"),
                ),
                "chunk_index": metadata.get("chunk_index"),
                "content_format": metadata.get("content_format", "markdown"),
            })
        return chunks

    # ==================== 统计 ====================

    def get_stats(self) -> dict:
        doc_count = self.collection.count()
        return {"document_chunks": doc_count, "total_chunks": doc_count}

    def reconcile_metadata(self, sql_documents: List[Dict]) -> dict:
        """只读对账 MySQL 文档元数据与 Chroma 分块，返回可操作的问题清单。"""
        issues: List[Dict] = []

        def add_issue(code: str, message: str, **details) -> None:
            issue = {"code": code, "message": message}
            issue.update(details)
            issues.append(issue)

        try:
            embedding_report = self.validate_embedding_config()
        except Exception as exc:
            logger.exception("RAG 健康检查读取 embedding 配置失败")
            embedding_report = {
                "consistent": False,
                "expected_model": self.embedding_model,
                "issues": [str(exc)],
            }
        if not embedding_report.get("consistent"):
            add_issue(
                "embedding_config_inconsistent",
                "Embedding 配置与现有向量索引不一致",
                details=list(embedding_report.get("issues") or []),
            )

        try:
            index_data = self.collection.get(include=["metadatas"])
        except Exception as exc:
            logger.exception("RAG 健康检查读取 Chroma 索引失败")
            add_issue("chroma_unavailable", "无法读取 Chroma 索引", details=str(exc))
            return {
                "healthy": False,
                "status": "degraded",
                "summary": {
                    "mysql_documents": len(sql_documents),
                    "chroma_documents": 0,
                    "mysql_chunks": sum(int(item.get("chunk_count") or 0) for item in sql_documents),
                    "chroma_chunks": 0,
                    "issue_count": len(issues),
                },
                "embedding": embedding_report,
                "issues": issues,
                "documents": [],
            }

        index_ids = list(index_data.get("ids") or [])
        index_metadatas = list(index_data.get("metadatas") or [])
        chroma_docs: Dict[str, Dict] = {}
        missing_doc_id_chunks = 0
        if len(index_metadatas) != len(index_ids):
            add_issue(
                "missing_chroma_metadata",
                "部分 Chroma 分块缺少 metadata",
                chunk_count=len(index_ids) - len(index_metadatas),
            )
        for position, record_id in enumerate(index_ids):
            metadata = index_metadatas[position] if position < len(index_metadatas) else {}
            metadata = metadata or {}
            doc_id = metadata.get("doc_id")
            if not doc_id:
                missing_doc_id_chunks += 1
                continue
            document = chroma_docs.setdefault(
                str(doc_id),
                {
                    "doc_id": str(doc_id),
                    "chunk_count": 0,
                    "filenames": set(),
                    "chunk_indexes": [],
                    "record_ids": [],
                },
            )
            document["chunk_count"] += 1
            document["record_ids"].append(record_id)
            if metadata.get("filename"):
                document["filenames"].add(str(metadata["filename"]))
            if metadata.get("chunk_index") is not None:
                document["chunk_indexes"].append(metadata["chunk_index"])

        if missing_doc_id_chunks:
            add_issue(
                "missing_chroma_doc_id",
                "部分 Chroma 分块缺少 doc_id 元数据",
                chunk_count=missing_doc_id_chunks,
            )

        for doc_id, document in chroma_docs.items():
            filenames = sorted(document["filenames"])
            if len(filenames) > 1:
                add_issue(
                    "mixed_chroma_filenames",
                    "同一 Chroma 文档包含多个文件名",
                    doc_id=doc_id,
                    filenames=filenames,
                )
            indexes = document["chunk_indexes"]
            if indexes and len(set(indexes)) != len(indexes):
                add_issue(
                    "duplicate_chunk_index",
                    "同一 Chroma 文档存在重复 chunk_index",
                    doc_id=doc_id,
                )

        sql_by_doc_id: Dict[str, List[Dict]] = {}
        for item in sql_documents:
            vector_doc_id = item.get("filename")
            if not vector_doc_id:
                add_issue(
                    "missing_mysql_doc_id",
                    "MySQL 知识库记录缺少向量 doc_id",
                    mysql_id=item.get("id"),
                )
                continue
            sql_by_doc_id.setdefault(str(vector_doc_id), []).append(item)

        for doc_id, rows in sql_by_doc_id.items():
            if len(rows) > 1:
                add_issue(
                    "duplicate_mysql_doc_id",
                    "多个 MySQL 记录指向同一 Chroma 文档",
                    doc_id=doc_id,
                    mysql_ids=[row.get("id") for row in rows],
                )

        document_results = []
        all_doc_ids = sorted(set(sql_by_doc_id) | set(chroma_docs))
        for doc_id in all_doc_ids:
            sql_rows = sql_by_doc_id.get(doc_id, [])
            chroma_document = chroma_docs.get(doc_id)
            sql_row = sql_rows[0] if sql_rows else None
            doc_issues = []

            if sql_row is None:
                code = "orphan_chroma_document"
                doc_issues.append(code)
                add_issue(
                    code,
                    "Chroma 文档没有对应的 MySQL 元数据",
                    doc_id=doc_id,
                    chroma_chunks=chroma_document["chunk_count"],
                )
            elif chroma_document is None:
                code = "missing_chroma_document"
                doc_issues.append(code)
                add_issue(
                    code,
                    "MySQL 文档在 Chroma 中不存在",
                    doc_id=doc_id,
                    mysql_id=sql_row.get("id"),
                )
            else:
                expected = int(sql_row.get("chunk_count") or 0)
                actual = int(chroma_document["chunk_count"])
                if expected != actual:
                    code = "chunk_count_mismatch"
                    doc_issues.append(code)
                    add_issue(
                        code,
                        "MySQL 与 Chroma 的分块数量不一致",
                        doc_id=doc_id,
                        expected=expected,
                        actual=actual,
                    )
                original_name = sql_row.get("original_name")
                filenames = chroma_document["filenames"]
                if original_name and filenames and original_name not in filenames:
                    code = "filename_mismatch"
                    doc_issues.append(code)
                    add_issue(
                        code,
                        "MySQL 原文件名与 Chroma 元数据不一致",
                        doc_id=doc_id,
                        mysql_filename=original_name,
                        chroma_filenames=sorted(filenames),
                    )

            document_results.append({
                "doc_id": doc_id,
                "original_name": sql_row.get("original_name") if sql_row else (
                    sorted(chroma_document["filenames"])[0]
                    if chroma_document and chroma_document["filenames"] else None
                ),
                "mysql_chunks": int(sql_row.get("chunk_count") or 0) if sql_row else None,
                "chroma_chunks": chroma_document["chunk_count"] if chroma_document else None,
                "status": "healthy" if not doc_issues else "degraded",
                "issues": doc_issues,
            })

        summary = {
            "mysql_documents": len(sql_documents),
            "chroma_documents": len(chroma_docs),
            "mysql_chunks": sum(int(item.get("chunk_count") or 0) for item in sql_documents),
            "chroma_chunks": len(index_ids),
            "issue_count": len(issues),
        }
        return {
            "healthy": not issues,
            "status": "healthy" if not issues else "degraded",
            "summary": summary,
            "embedding": {
                "consistent": bool(embedding_report.get("consistent")),
                "expected_model": embedding_report.get("expected_model"),
                "issues": list(embedding_report.get("issues") or []),
            },
            "issues": issues,
            "documents": document_results,
        }


knowledge_service = KnowledgeService()
