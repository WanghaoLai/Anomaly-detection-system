"""Markdown 语义切分组件。"""

from __future__ import annotations

import re

from .contracts import Document, Node
from .loaders import MARKDOWN_FENCE_RE, MARKDOWN_HEADING_RE


def approx_token_len(text: str) -> int:
    def is_cjk(ch: str) -> bool:
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

    return sum(1 for ch in text if is_cjk(ch)) + len(
        [token for token in text.split() if token]
    )


def _heading_markdown(heading_stack: list[tuple[int, str]]) -> str:
    return "\n".join(
        f"{'#' * level} {title}".rstrip() for level, title in heading_stack
    )


def split_paragraphs_with_headings(text: str) -> list[dict]:
    if not text or not text.strip():
        return []
    lines = text.splitlines(keepends=True)
    heading_stack: list[tuple[int, str]] = []
    paragraphs: list[dict] = []
    buf: list[str] = []
    buf_start: int | None = None
    char_pos = 0
    fence: tuple[str, int] | None = None

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
        paragraphs.append({
            "content": content,
            "heading_path": (
                " > ".join(title for _, title in heading_stack)
                if heading_stack else None
            ),
            "heading_markdown": _heading_markdown(heading_stack),
            "start": max(0, start),
            "end": min(len(text), end_pos),
        })

    for line_with_ending in lines:
        raw = line_with_ending.rstrip("\r\n")
        line_end = char_pos + len(line_with_ending)
        fence_match = MARKDOWN_FENCE_RE.match(raw)
        if fence is not None:
            if (fence_match and fence_match.group("fence")[0] == fence[0]
                    and len(fence_match.group("fence")) >= fence[1]):
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
        heading_match = MARKDOWN_HEADING_RE.match(raw)
        if heading_match:
            flush_buf(char_pos)
            level = len(heading_match.group("marks"))
            title = re.sub(
                r"[ \t]+#+[ \t]*$", "", (heading_match.group("title") or "").strip()
            ).strip()
            if level <= len(heading_stack):
                heading_stack = heading_stack[:level - 1]
            heading_stack.append((level, title))
            char_pos = line_end
            continue
        if not raw.strip():
            flush_buf(char_pos)
            char_pos = line_end
            continue
        if buf_start is None:
            buf_start = char_pos
        buf.append(raw)
        char_pos = line_end
    flush_buf(char_pos)
    if not paragraphs and text.strip():
        paragraphs = [{
            "content": text.strip(),
            "heading_path": None,
            "heading_markdown": "",
            "start": 0,
            "end": len(text),
        }]
    return paragraphs


def _paragraph_token_len(paragraph: dict) -> int:
    heading = paragraph.get("heading_markdown") or ""
    content = paragraph.get("content") or ""
    rendered = f"{heading}\n\n{content}" if heading else content
    return approx_token_len(rendered) or 1


def _contains_fenced_code(text: str) -> bool:
    return sum(1 for line in text.splitlines() if MARKDOWN_FENCE_RE.match(line)) >= 2


def _split_text_by_token_budget(text: str, chunk_tokens: int) -> list[str]:
    if approx_token_len(text) <= chunk_tokens:
        return [text]
    units = re.findall(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]|[^\s]+|\s+", text)
    parts: list[str] = []
    current = ""
    for unit in units:
        candidate = current + unit
        if current.strip() and approx_token_len(candidate) > chunk_tokens:
            parts.append(current.strip())
            current = unit.lstrip()
        else:
            current = candidate
    if current.strip():
        parts.append(current.strip())
    return parts or [text]


def _split_oversized_paragraph(paragraph: dict, chunk_tokens: int) -> list[dict]:
    if _paragraph_token_len(paragraph) <= chunk_tokens or _contains_fenced_code(
        paragraph.get("content") or ""
    ):
        item = dict(paragraph)
        item["token_count"] = _paragraph_token_len(item)
        return [item]
    content = paragraph.get("content") or ""
    heading_tokens = approx_token_len(paragraph.get("heading_markdown") or "")
    parts = _split_text_by_token_budget(content, max(1, chunk_tokens - heading_tokens))
    result: list[dict] = []
    search_from = 0
    for part in parts:
        relative_start = content.find(part, search_from)
        if relative_start < 0:
            relative_start = search_from
        relative_end = relative_start + len(part)
        search_from = relative_end
        item = dict(paragraph)
        item.update({
            "content": part,
            "start": paragraph.get("start", 0) + relative_start,
            "end": paragraph.get("start", 0) + relative_end,
        })
        item["token_count"] = _paragraph_token_len(item)
        result.append(item)
    return result


def _render_chunk_markdown(paragraphs: list[dict]) -> str:
    rendered: list[str] = []
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


def chunk_paragraphs(
    paragraphs: list[dict],
    chunk_tokens: int,
    overlap_tokens: int,
) -> list[dict]:
    if chunk_tokens <= 0:
        raise ValueError("chunk_tokens 必须大于 0")
    if overlap_tokens < 0 or overlap_tokens >= chunk_tokens:
        raise ValueError("overlap_tokens 必须满足 0 <= overlap_tokens < chunk_tokens")
    normalized: list[dict] = []
    for paragraph in paragraphs:
        normalized.extend(_split_oversized_paragraph(paragraph, chunk_tokens))
    chunks: list[dict] = []
    start_index = 0
    while start_index < len(normalized):
        current: list[dict] = []
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
        heading_paths: list[str] = []
        for item in current:
            path = item.get("heading_path")
            if path and path not in heading_paths:
                heading_paths.append(path)
        chunks.append({
            "content": content,
            "start": current[0].get("start", 0),
            "end": current[-1].get("end", 0),
            "heading_path": heading_paths[0] if heading_paths else None,
            "heading_paths": " | ".join(heading_paths) if heading_paths else None,
            "token_count": approx_token_len(content) or 1,
            "paragraph_count": len(current),
        })
        if end_index >= len(normalized):
            break
        next_start = end_index
        kept_tokens = 0
        while next_start > start_index:
            candidate = normalized[next_start - 1]
            candidate_tokens = candidate.get("token_count") or _paragraph_token_len(candidate)
            if kept_tokens + candidate_tokens > overlap_tokens:
                break
            next_start -= 1
            kept_tokens += candidate_tokens
        start_index = max(start_index + 1, next_start)
    return chunks


class MarkdownNodeParser:
    def __init__(self, chunk_tokens: int, overlap_tokens: int):
        self.chunk_tokens = int(chunk_tokens)
        self.overlap_tokens = int(overlap_tokens)
        # 用核心函数统一校验配置。
        chunk_paragraphs([], self.chunk_tokens, self.overlap_tokens)

    def parse(self, document: Document) -> list[Node]:
        records = chunk_paragraphs(
            split_paragraphs_with_headings(document.text),
            self.chunk_tokens,
            self.overlap_tokens,
        )
        return [Node(text=record.pop("content"), metadata=record) for record in records]
