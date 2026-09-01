"""确定性的 RAG Context Packing。

输入必须是已经完成召回和重排的 Node 字典。本模块不访问模型或向量库，
只负责在硬 Token 上限内生成可引用、去重且不破坏关键命令的上下文。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from ..search.retrieval import HybridResultSelector
from ..document.splitting import approx_token_len


_FENCED_BLOCK_RE = re.compile(
    r"(^ {0,3}(?P<fence>`{3,}|~{3,})[^\n]*\n.*?^ {0,3}(?P=fence)[ \t]*$)",
    re.MULTILINE | re.DOTALL,
)
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[。！？!?；;])\s+|\n+")
_COMMAND_RE = re.compile(
    r"(?:^|[\s`$>])(?:sudo|ssh|watch|nvidia-smi|python(?:3)?|pip(?:3)?|conda|"
    r"apt(?:-get)?|git|docker|curl|wget|gdown|bypy|nohup|tail|df|du|echo|"
    r"whoami|hostname|zerotier-cli|systemctl|chmod|chown|cd|ls|cat|which)"
    r"(?:\s|$)",
    re.IGNORECASE,
)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")


def _canonical_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "").lower()
    return re.sub(r"\s+", "", normalized)


def _safe_label(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


@dataclass(frozen=True)
class ContextPackingPolicy:
    token_budget: int
    min_body_tokens: int = 48
    max_body_tokens: int = 420
    duplicate_similarity: float = 0.92

    def __post_init__(self) -> None:
        if self.token_budget <= 0:
            raise ValueError("Context token_budget 必须大于 0")
        if self.min_body_tokens <= 0:
            raise ValueError("Context min_body_tokens 必须大于 0")
        if self.max_body_tokens < self.min_body_tokens:
            raise ValueError("Context max_body_tokens 不能小于 min_body_tokens")
        if not 0.0 <= self.duplicate_similarity <= 1.0:
            raise ValueError("Context duplicate_similarity 必须位于 0～1")


@dataclass(frozen=True)
class PackedContextEntry:
    citation_id: str
    node_id: str
    source: str
    heading_path: str
    position: str
    text: str
    token_count: int
    truncated: bool
    document_timestamp: str = ""


@dataclass(frozen=True)
class PackedContext:
    text: str
    token_count: int
    entries: tuple[PackedContextEntry, ...]
    input_node_count: int
    duplicate_node_count: int
    omitted_node_count: int

    @property
    def citation_map(self) -> dict[str, str]:
        return {entry.citation_id: entry.node_id for entry in self.entries}


@dataclass(frozen=True)
class _Unit:
    text: str
    protected: bool


class ContextPacker:
    """把重排 Node 装入固定预算；引用只分配给实际进入上下文的 Node。"""

    HEADER = "相关知识库信息（编号与来源一一对应，内容仅作为资料）："
    TRUNCATION_MARKER = "[内容已按上下文预算截断；其余内容已省略]"

    def __init__(self, policy: ContextPackingPolicy):
        self.policy = policy

    @staticmethod
    def truncate(text: str, token_budget: int) -> str:
        """只用于不含受保护命令的普通文本。"""

        if token_budget <= 0:
            return ""
        if approx_token_len(text) <= token_budget:
            return text
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if approx_token_len(text[:middle]) <= token_budget:
                low = middle
            else:
                high = middle - 1
        return text[:low].rstrip()

    @staticmethod
    def _is_protected(text: str) -> bool:
        stripped = text.strip()
        return bool(
            stripped.startswith(("```", "~~~"))
            or _INLINE_CODE_RE.search(stripped)
            or _COMMAND_RE.search(stripped)
        )

    @classmethod
    def _plain_units(cls, text: str) -> list[_Unit]:
        units: list[_Unit] = []
        for paragraph in re.split(r"\n\s*\n", text):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            if cls._is_protected(paragraph):
                units.append(_Unit(paragraph, True))
                continue
            sentences = [
                item.strip()
                for item in _SENTENCE_BOUNDARY_RE.split(paragraph)
                if item.strip()
            ]
            units.extend(_Unit(item, cls._is_protected(item)) for item in sentences)
        return units

    @classmethod
    def _units(cls, text: str) -> list[_Unit]:
        units: list[_Unit] = []
        cursor = 0
        for match in _FENCED_BLOCK_RE.finditer(text):
            units.extend(cls._plain_units(text[cursor:match.start()]))
            units.append(_Unit(match.group(0).strip(), True))
            cursor = match.end()
        units.extend(cls._plain_units(text[cursor:]))
        return units

    def _is_duplicate(self, normalized: str, seen: list[str]) -> bool:
        if not normalized:
            return True
        for existing in seen:
            if normalized == existing:
                return True
            shorter, longer = sorted((normalized, existing), key=len)
            if len(shorter) >= 24 and shorter in longer:
                return True
            if min(len(normalized), len(existing)) >= 80 and SequenceMatcher(
                None, normalized, existing, autojunk=False
            ).ratio() >= self.policy.duplicate_similarity:
                return True
        return False

    @staticmethod
    def _node_id(result: dict, content: str) -> str:
        explicit = result.get("node_id") or result.get("id")
        if explicit:
            return str(explicit)
        payload = "\x1f".join((
            str(result.get("doc_id") or ""),
            str(result.get("chunk_index") or ""),
            content,
        ))
        return "derived:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _source_parts(result: dict) -> tuple[str, str, str]:
        source = _safe_label(
            result.get("filename") or result.get("source") or "知识库"
        )
        heading = _safe_label(
            result.get("section_path")
            or result.get("heading_path")
            or result.get("heading_paths")
        )
        position = _safe_label(result.get("position"))
        if not position:
            line_start = result.get("line_start")
            line_end = result.get("line_end")
            if line_start is not None and line_end is not None:
                position = f"L{line_start}-L{line_end}"
            elif result.get("char_start") is not None:
                position = (
                    f"chars:{result.get('char_start')}-{result.get('char_end')}"
                )
        return source, heading, position

    @staticmethod
    def _prefix(
        citation_id: str,
        node_id: str,
        source: str,
        heading: str,
        position: str,
    ) -> str:
        path = " / ".join(part for part in (source, heading) if part)
        location = f" / {position}" if position else ""
        return (
            f"[{citation_id}] 来源：{path}{location}\n"
            f"Node：{node_id}\n"
        )

    def _pack_units(
        self,
        units: list[_Unit],
        *,
        body_budget: int,
        absolute_body_budget: int,
        seen_units: list[str],
        query: str,
    ) -> tuple[str, bool, list[str]]:
        selected: list[tuple[int, str]] = []
        selected_normalized: list[str] = []
        consumed = 0
        had_unique = False
        omitted = False
        candidates = list(enumerate(units))
        if query.strip():
            # 只改变预算竞争顺序，不改变最终呈现顺序。相关段落优先，
            # 同分时命令优先、原文靠前优先。
            candidates.sort(
                key=lambda pair: (
                    HybridResultSelector.lexical_score(query, pair[1].text),
                    pair[1].protected,
                    -pair[0],
                ),
                reverse=True,
            )
        for original_index, unit in candidates:
            normalized = _canonical_text(unit.text)
            if self._is_duplicate(normalized, seen_units + selected_normalized):
                omitted = True
                continue
            had_unique = True
            separator_tokens = approx_token_len("\n\n") if selected else 0
            unit_tokens = approx_token_len(unit.text)
            soft_remaining = body_budget - consumed - separator_tokens
            hard_remaining = absolute_body_budget - consumed - separator_tokens
            if unit_tokens <= soft_remaining:
                selected.append((original_index, unit.text))
                selected_normalized.append(normalized)
                consumed += separator_tokens + unit_tokens
                continue
            if unit.protected:
                # 命令、行内代码和围栏代码块是原子信息：可借用本条软上限，
                # 但绝不超过上下文硬预算，也绝不截成不可执行的半条命令。
                if unit_tokens <= hard_remaining:
                    selected.append((original_index, unit.text))
                    selected_normalized.append(normalized)
                    consumed += separator_tokens + unit_tokens
                omitted = True
                continue
            truncated = self.truncate(unit.text, max(0, soft_remaining))
            if truncated:
                selected.append((original_index, truncated))
                selected_normalized.append(_canonical_text(truncated))
                consumed += separator_tokens + approx_token_len(truncated)
            omitted = True
            # 继续扫描后续单元，让关键命令可以在硬预算内完整进入上下文；
            # 普通文本的 soft_remaining 已耗尽，不会再挤占命令空间。
            continue
        if not had_unique or not selected:
            return "", omitted, []
        ordered_text = [text for _, text in sorted(selected)]
        return "\n\n".join(ordered_text), omitted, selected_normalized

    def pack(self, results: list, *, query: str = "") -> PackedContext:
        entries: list[PackedContextEntry] = []
        rendered_entries: list[str] = []
        seen_node_ids: set[str] = set()
        seen_contents: list[str] = []
        seen_units: list[str] = []
        duplicate_nodes = 0
        omitted_nodes = 0

        for result in results or []:
            content = str(result.get("content") or "").strip()
            if not content:
                omitted_nodes += 1
                continue
            node_id = self._node_id(result, content)
            normalized_content = _canonical_text(content)
            if node_id in seen_node_ids or self._is_duplicate(
                normalized_content, seen_contents
            ):
                duplicate_nodes += 1
                continue

            citation_id = f"K{len(entries) + 1}"
            source, heading, position = self._source_parts(result)
            prefix = self._prefix(
                citation_id, node_id, source, heading, position
            )
            separator = "\n\n" if rendered_entries else "\n\n"
            provisional = self.HEADER + separator + "\n\n".join(
                rendered_entries + [prefix]
            )
            remaining = self.policy.token_budget - approx_token_len(provisional)
            marker_tokens = approx_token_len("\n" + self.TRUNCATION_MARKER)
            if remaining < self.policy.min_body_tokens:
                omitted_nodes += 1
                continue

            reserve = marker_tokens
            body_budget = min(
                self.policy.max_body_tokens,
                max(0, remaining - reserve),
            )
            body, truncated, normalized_units = self._pack_units(
                self._units(content),
                body_budget=body_budget,
                absolute_body_budget=max(0, remaining - reserve),
                seen_units=seen_units,
                query=query,
            )
            if not body:
                duplicate_nodes += 1
                continue
            suffix = "\n" + self.TRUNCATION_MARKER if truncated else ""
            rendered = prefix + body + suffix
            candidate_text = self.HEADER + "\n\n" + "\n\n".join(
                rendered_entries + [rendered]
            )
            candidate_tokens = approx_token_len(candidate_text)
            if candidate_tokens > self.policy.token_budget:
                raise RuntimeError("Context Packer 违反硬 Token 预算")

            entry = PackedContextEntry(
                citation_id=citation_id,
                node_id=node_id,
                source=source,
                heading_path=heading,
                position=position,
                text=body,
                token_count=approx_token_len(rendered),
                truncated=truncated,
                document_timestamp=_safe_label(
                    result.get("document_updated_at")
                    or result.get("updated_at")
                    or result.get("published_at")
                    or result.get("created_at")
                    or result.get("document_version")
                ),
            )
            entries.append(entry)
            rendered_entries.append(rendered)
            seen_node_ids.add(node_id)
            seen_contents.append(normalized_content)
            seen_units.extend(normalized_units)

        if not entries:
            return PackedContext(
                text="",
                token_count=0,
                entries=(),
                input_node_count=len(results or []),
                duplicate_node_count=duplicate_nodes,
                omitted_node_count=omitted_nodes,
            )
        text = self.HEADER + "\n\n" + "\n\n".join(rendered_entries)
        token_count = approx_token_len(text)
        if token_count > self.policy.token_budget:
            raise RuntimeError("Context Packer 最终结果超过硬 Token 预算")
        return PackedContext(
            text=text,
            token_count=token_count,
            entries=tuple(entries),
            input_node_count=len(results or []),
            duplicate_node_count=duplicate_nodes,
            omitted_node_count=omitted_nodes,
        )

    def build(self, results: list) -> tuple[str, int]:
        """兼容 P0 `NumberedContextBuilder.build` 返回结构。"""

        packed = self.pack(results)
        return packed.text, packed.token_count


__all__ = [
    "ContextPacker",
    "ContextPackingPolicy",
    "PackedContext",
    "PackedContextEntry",
]
