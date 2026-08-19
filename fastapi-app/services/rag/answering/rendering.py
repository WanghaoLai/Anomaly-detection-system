"""服务端确定性渲染：把已验证 claims 转为用户可见的 Markdown。

模型只提交纯文本 claims；列表结构、行内代码、引用编号的排版全部由
本模块决定，与 Grounding 校验共用同一套原子定义，展示层无法夹带
未经验证的内容。
"""

from __future__ import annotations

import re

from .atoms import (
    COMMAND_NAMES,
    COMMAND_WORD_RE,
    EXACT_ATOM_RE,
    TECH_TOKEN_RE,
    contains_command,
)

_INLINE_CODE_SPLIT_RE = re.compile(r"(`[^`\n]+`)")
_REDIRECT_PATH_RE = re.compile(
    r"(?<![\w])\d*(?:>>?)(?:/(?:[\w.+-]+/)*[\w.+-]*)"
)
_SENTENCE_END_RE = re.compile(r"(?<=[。！？!?；;\n])")
_SNIPPET_MAX_CHARS = 140


def _renderable_atom(token: str) -> bool:
    """只渲染"看起来像机器片段"的 token：含数字、分隔符或已知命令。

    普通英文词（GPU、Python）保持正文字体，避免整段都是代码样式。
    """

    return (
        any(character.isdigit() for character in token)
        or any(character in "-._:/\\" for character in token)
        or token.lower() in COMMAND_NAMES
    )


def _merge_spans(spans: list[list[int]]) -> list[list[int]]:
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _wrap_atoms(segment: str) -> str:
    spans: list[list[int]] = []
    for match in EXACT_ATOM_RE.finditer(segment):
        atom = next((item for item in match.groups() if item), "")
        if atom:
            spans.append([match.start(), match.end()])
    for match in TECH_TOKEN_RE.finditer(segment):
        if _renderable_atom(match.group(0)):
            spans.append([match.start(), match.end()])
    # 技术通配的正则要求 token 至少 3 个字符，df、du 这类短命令需单独补齐；
    # 重定向写法 2>/dev/null 也整体成段，避免 ">" 游离在代码样式之外。
    for match in COMMAND_WORD_RE.finditer(segment):
        spans.append([match.start(), match.end()])
    for match in _REDIRECT_PATH_RE.finditer(segment):
        spans.append([match.start(), match.end()])
    if not spans:
        return segment

    merged = _merge_spans(spans)
    for index, (start, end) in enumerate(merged):
        # 命令与其单空格相邻的参数融合成一条完整命令，如 df -h。
        if segment[start:end].lower() in COMMAND_NAMES:
            while (
                index + 1 < len(merged)
                and merged[index + 1][0] == end + 1
                and segment[end:end + 1] == " "
            ):
                end = merged[index + 1][1]
                del merged[index + 1]
            merged[index] = [start, end]
    # 裸数字（如 RTX 4090 的 4090）不是独立的技术片段，保持正文字体；
    # 已随命令融合的数字参数不受影响。
    wrapped = [
        (start, end) for start, end in merged
        if not segment[start:end].replace(".", "").isdigit()
    ]

    parts: list[str] = []
    cursor = 0
    for start, end in wrapped:
        parts.append(segment[cursor:start])
        parts.append(f"`{segment[start:end]}`")
        cursor = end
    parts.append(segment[cursor:])
    return "".join(parts)


def render_inline(text: str) -> str:
    """保留模型已写的行内代码，其余技术片段按原子规则补充反引号。"""

    rendered: list[str] = []
    for index, part in enumerate(_INLINE_CODE_SPLIT_RE.split(text or "")):
        # split 带捕获组时，奇数下标是 `...` 代码段，原样保留。
        rendered.append(part if index % 2 == 1 else _wrap_atoms(part))
    return "".join(rendered).strip()


class AnswerRenderer:
    """已验证 claims → Markdown；模型无权决定展示层。"""

    @staticmethod
    def render_claims(claims) -> str:
        items: list[str] = []
        has_command = False
        for claim in claims:
            inline = render_inline(claim.text)
            citations = " ".join(f"[{citation}]" for citation in claim.citations)
            items.append(f"{inline} {citations}".strip())
            has_command = has_command or contains_command(claim.text)
        if not items:
            return ""
        if len(items) == 1:
            return items[0]
        if has_command:
            return "\n".join(
                f"{index}. {item}" for index, item in enumerate(items, start=1)
            )
        return "\n".join(f"- {item}" for item in items)

    @staticmethod
    def snippet(text: str, max_chars: int = _SNIPPET_MAX_CHARS) -> str:
        collapsed = re.sub(r"\s+", " ", str(text or "")).strip()
        if len(collapsed) <= max_chars:
            return collapsed
        return collapsed[:max_chars].rstrip() + "…"

    @staticmethod
    def chunk_answer(text: str, max_chars: int = 96) -> list[str]:
        """按句子/换行边界切片，避免把引用编号和 Markdown 语法切开。"""

        if not text:
            return []
        if max_chars <= 0:
            return [text]
        segments: list[str] = []
        for piece in _SENTENCE_END_RE.split(text):
            while len(piece) > max_chars:
                segments.append(piece[:max_chars])
                piece = piece[max_chars:]
            if piece:
                segments.append(piece)
        chunks: list[str] = []
        current = ""
        for segment in segments:
            if current and len(current) + len(segment) > max_chars:
                chunks.append(current)
                current = segment
            else:
                current += segment
        if current:
            chunks.append(current)
        return chunks


__all__ = ["AnswerRenderer", "render_inline"]
