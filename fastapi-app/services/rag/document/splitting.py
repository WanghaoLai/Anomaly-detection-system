"""Markdown 语义分块算法。

设计上先保证语义单元完整，再追求 Token 区间：围栏代码、命令块、
表格和缩进代码不会被拦腰切断。当它们自身超过上限时，保留完整内容并在
metadata 中标记超限，由后续上下文构建层决定是否摘要。
"""

from __future__ import annotations

import re
from typing import Callable, Sequence

from .markdown import MARKDOWN_FENCE_RE, MARKDOWN_HEADING_RE


PARSER_SCHEMA_VERSION = "llamaindex-markdown-node-v1"
DEFAULT_TARGET_RATIO = 0.8
DEFAULT_MIN_RATIO = 0.2

_COMMAND_RE = re.compile(
    r"^\s*(?:(?:\$|>)\s+)?(?:sudo\s+)?(?:"
    r"apt(?:-get)?|yum|dnf|brew|pip\d*|python\d*|conda|npm|pnpm|yarn|"
    r"docker(?:\s+compose)?|kubectl|helm|systemctl|journalctl|"
    r"curl|wget|ssh|scp|rsync|git|bash|sh|zsh|powershell|cmd|"
    r"chmod|chown|export|source|cd|mkdir|cp|mv|tar|unzip|"
    r"grep|rg|awk|sed|mysql|psql|redis-cli"
    r")(?:\s|$)",
    re.IGNORECASE,
)
_TABLE_SEPARATOR_RE = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$"
)
_SENTENCE_UNIT_RE = re.compile(r".*?(?:[\u3002！？；!?;]+(?=\s|$)|\n+|$)", re.DOTALL)
_TOKEN_UNIT_RE = re.compile(
    r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]|[^\s]+|\s+"
)


def approx_token_len(text: str) -> int:
    """P0 兼容估算器；旧调用方与基线测试保持原行为。"""

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


def _block_type(content: str) -> tuple[str, bool]:
    stripped = content.strip()
    lines = stripped.splitlines()
    fence_count = sum(1 for line in lines if MARKDOWN_FENCE_RE.match(line))
    if fence_count >= 2:
        return "fenced_code", True
    nonempty = [line for line in lines if line.strip()]
    if len(nonempty) >= 2 and (
        any(_TABLE_SEPARATOR_RE.match(line) for line in nonempty)
        or all(line.lstrip().startswith("|") for line in nonempty)
    ):
        return "table", True
    if nonempty and all(line.startswith(("    ", "\t")) for line in nonempty):
        return "indented_code", True
    command_lines = [line for line in nonempty if _COMMAND_RE.match(line)]
    if command_lines:
        return "command", True
    return "paragraph", False


def split_paragraphs_with_headings(text: str) -> list[dict]:
    """Markdown 转成带标题路径和原文位置的语义块。"""

    if not text or not text.strip():
        return []
    lines = text.splitlines(keepends=True)
    heading_stack: list[tuple[int, str]] = []
    paragraphs: list[dict] = []
    buf: list[str] = []
    buf_start: int | None = None
    char_pos = 0
    fence: tuple[str, int] | None = None

    def append_record(content: str, start: int, end: int) -> None:
        value = content.strip()
        if not value:
            return
        block_type, protected = _block_type(value)
        paragraphs.append({
            "content": value,
            "heading_path": (
                " > ".join(title for _, title in heading_stack)
                if heading_stack else None
            ),
            "heading_markdown": _heading_markdown(heading_stack),
            "start": max(0, start),
            "end": min(len(text), end),
            "block_type": block_type,
            "protected": protected,
        })

    def flush_buf(end_pos: int) -> None:
        nonlocal buf, buf_start
        if not buf:
            return
        content = "\n".join(buf)
        start = buf_start if buf_start is not None else max(0, end_pos - len(content))
        buf = []
        buf_start = None
        append_record(content, start, end_pos)

    for line_with_ending in lines:
        raw = line_with_ending.rstrip("\r\n")
        line_end = char_pos + len(line_with_ending)
        fence_match = MARKDOWN_FENCE_RE.match(raw)
        if fence is not None:
            if buf_start is None:
                buf_start = char_pos
            buf.append(raw)
            if (
                fence_match
                and fence_match.group("fence")[0] == fence[0]
                and len(fence_match.group("fence")) >= fence[1]
            ):
                fence = None
                flush_buf(line_end)
            char_pos = line_end
            continue
        if fence_match:
            flush_buf(char_pos)
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
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
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
        block_type, protected = _block_type(text)
        paragraphs = [{
            "content": text.strip(),
            "heading_path": None,
            "heading_markdown": "",
            "start": 0,
            "end": len(text),
            "block_type": block_type,
            "protected": protected,
        }]
    return paragraphs


def _render_paragraph(paragraph: dict) -> str:
    heading = paragraph.get("heading_markdown") or ""
    content = paragraph.get("content") or ""
    return f"{heading}\n\n{content}" if heading else content


def _paragraph_token_len(
    paragraph: dict,
    token_counter: Callable[[str], int] = approx_token_len,
) -> int:
    return token_counter(_render_paragraph(paragraph)) or 1


def _find_nonempty_parts(text: str, parts: Sequence[str], base_start: int) -> list[tuple[str, int, int]]:
    result: list[tuple[str, int, int]] = []
    search_from = 0
    for raw_part in parts:
        part = raw_part.strip()
        if not part:
            continue
        relative_start = text.find(part, search_from)
        if relative_start < 0:
            relative_start = search_from
        relative_end = relative_start + len(part)
        search_from = relative_end
        result.append((part, base_start + relative_start, base_start + relative_end))
    return result


def _split_by_token_budget(
    text: str,
    budget: int,
    token_counter: Callable[[str], int],
) -> list[str]:
    if token_counter(text) <= budget:
        return [text]
    parts: list[str] = []
    current = ""
    for match in _TOKEN_UNIT_RE.finditer(text):
        unit = match.group(0)
        candidate = current + unit
        if current.strip() and token_counter(candidate) > budget:
            parts.append(current)
            current = unit.lstrip()
        else:
            current = candidate
    if current.strip():
        parts.append(current)
    return parts or [text]


def _split_regular_paragraph(
    paragraph: dict,
    chunk_tokens: int,
    token_counter: Callable[[str], int],
) -> list[dict]:
    if _paragraph_token_len(paragraph, token_counter) <= chunk_tokens:
        item = dict(paragraph)
        item["token_count"] = _paragraph_token_len(item, token_counter)
        return [item]
    if paragraph.get("protected"):
        item = dict(paragraph)
        item["token_count"] = _paragraph_token_len(item, token_counter)
        item["oversized_protected"] = item["token_count"] > chunk_tokens
        return [item]

    content = str(paragraph.get("content") or "")
    heading_tokens = token_counter(str(paragraph.get("heading_markdown") or ""))
    content_budget = max(1, chunk_tokens - heading_tokens)
    sentence_parts = [
        match.group(0) for match in _SENTENCE_UNIT_RE.finditer(content)
        if match.group(0).strip()
    ]
    packed: list[str] = []
    current = ""
    for sentence in sentence_parts or [content]:
        candidate = current + sentence
        if current.strip() and token_counter(candidate) > content_budget:
            packed.append(current)
            current = sentence.lstrip()
        elif token_counter(sentence) > content_budget and not current.strip():
            packed.extend(
                _split_by_token_budget(sentence, content_budget, token_counter)
            )
            current = ""
        else:
            current = candidate
    if current.strip():
        packed.append(current)

    located = _find_nonempty_parts(
        content, packed, int(paragraph.get("start") or 0)
    )
    result: list[dict] = []
    for part, start, end in located:
        item = dict(paragraph)
        item.update({"content": part, "start": start, "end": end})
        item["token_count"] = _paragraph_token_len(item, token_counter)
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
        content = str(paragraph.get("content") or "").strip()
        if content:
            rendered.append(content)
    return "\n\n".join(rendered).strip()


def chunk_paragraphs(
    paragraphs: list[dict],
    chunk_tokens: int,
    overlap_tokens: int,
    *,
    target_ratio: float = DEFAULT_TARGET_RATIO,
    min_ratio: float = DEFAULT_MIN_RATIO,
    token_counter: Callable[[str], int] = approx_token_len,
) -> list[dict]:
    """按完整语义单元聚合，返回可直接构造 TextNode 的稳定记录。"""

    if chunk_tokens <= 0:
        raise ValueError("chunk_tokens 必须大于 0")
    if overlap_tokens < 0 or overlap_tokens >= chunk_tokens:
        raise ValueError("overlap_tokens 必须满足 0 <= overlap_tokens < chunk_tokens")
    if not 0 < min_ratio <= target_ratio <= 1:
        raise ValueError("Token 比例必须满足 0 < min_ratio <= target_ratio <= 1")
    target_tokens = max(1, round(chunk_tokens * target_ratio))
    min_tokens = max(1, round(chunk_tokens * min_ratio))

    normalized: list[dict] = []
    for paragraph in paragraphs:
        normalized.extend(
            _split_regular_paragraph(paragraph, chunk_tokens, token_counter)
        )
    chunks: list[dict] = []
    start_index = 0
    while start_index < len(normalized):
        current: list[dict] = []
        end_index = start_index
        while end_index < len(normalized):
            candidate = current + [normalized[end_index]]
            candidate_tokens = token_counter(_render_chunk_markdown(candidate)) or 1
            if current and candidate_tokens > chunk_tokens:
                break
            current = candidate
            end_index += 1
            if candidate_tokens >= target_tokens:
                # 目标值是软上限；若下一语义单元会越过硬上限，立即收敛。
                if end_index >= len(normalized):
                    break
                next_tokens = token_counter(
                    _render_chunk_markdown(current + [normalized[end_index]])
                )
                if next_tokens > chunk_tokens:
                    break
        if not current:
            current = [normalized[start_index]]
            end_index = start_index + 1
        content = _render_chunk_markdown(current)
        heading_paths: list[str] = []
        block_types: list[str] = []
        for item in current:
            path = item.get("heading_path")
            if path and path not in heading_paths:
                heading_paths.append(str(path))
            block_type = str(item.get("block_type") or "paragraph")
            if block_type not in block_types:
                block_types.append(block_type)
        observed_tokens = token_counter(content) or 1
        chunks.append({
            "content": content,
            "start": int(current[0].get("start") or 0),
            "end": int(current[-1].get("end") or 0),
            "heading_path": heading_paths[0] if heading_paths else None,
            "heading_paths": " | ".join(heading_paths) if heading_paths else None,
            "section_path": heading_paths[0] if heading_paths else "[root]",
            "token_count": observed_tokens,
            "token_min": min_tokens,
            "token_target": target_tokens,
            "token_max": chunk_tokens,
            "within_target_range": min_tokens <= observed_tokens <= chunk_tokens,
            "paragraph_count": len(current),
            "block_types": " | ".join(block_types),
            "protected": any(bool(item.get("protected")) for item in current),
            "oversized_protected": any(
                bool(item.get("oversized_protected")) for item in current
            ),
        })
        if end_index >= len(normalized):
            break
        next_start = end_index
        kept_tokens = 0
        while next_start > start_index:
            candidate = normalized[next_start - 1]
            candidate_tokens = int(
                candidate.get("token_count")
                or _paragraph_token_len(candidate, token_counter)
            )
            if kept_tokens + candidate_tokens > overlap_tokens:
                break
            next_start -= 1
            kept_tokens += candidate_tokens
        start_index = max(start_index + 1, next_start)
    return chunks


__all__ = [
    "DEFAULT_MIN_RATIO",
    "DEFAULT_TARGET_RATIO",
    "PARSER_SCHEMA_VERSION",
    "approx_token_len",
    "chunk_paragraphs",
    "split_paragraphs_with_headings",
]
