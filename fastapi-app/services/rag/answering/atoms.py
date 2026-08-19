"""原子片段提取：命令、路径、数值等技术片段的统一识别。

校验（判断 claim 是否被证据支持）与渲染（把技术片段转为行内代码）
共用同一套原子定义，保证"校验看到的内容"与"用户看到的内容"一致。
"""

from __future__ import annotations

import re
import unicodedata

EXACT_ATOM_RE = re.compile(
    r"`([^`\n]+)`|"
    r"((?:https?://|www\.)[^\s)\]，。]+)|"
    r"([A-Za-z]:\\[^\s，。]+|/(?:[\w.+-]+/)+[\w.+-]*)|"
    r"(\b\d+(?:\.\d+)?\s*(?:GB|MB|TB|秒|分钟|小时|%|端口)\b)",
    re.IGNORECASE,
)
TECH_TOKEN_RE = re.compile(
    r"(?<![\w])(?:--?[A-Za-z][\w-]*|\d+(?:\.\d+)?|"
    r"[A-Za-z][A-Za-z0-9_.:/\\-]{2,})(?![\w])"
)
COMMAND_NAMES = frozenset({
    "ssh", "sudo", "watch", "nvidia-smi", "python", "python3", "pip",
    "pip3", "conda", "apt", "git", "curl", "wget", "nohup", "tail",
    "df", "du", "echo", "whoami", "hostname", "zerotier-cli",
})
COMMAND_WORD_RE = re.compile(
    r"(?<![\w])(?:" + "|".join(sorted(COMMAND_NAMES)) + r")(?![\w])",
    re.IGNORECASE,
)


def compact_text(text: str) -> str:
    """NFKC + casefold + 去除全部空白，用于原子的格式等价比较。

    PDF 提取会在数值与单位、URL 内部插入空格和换行（如 "400 GB"、
    拆行的链接），模型按惯例书写紧凑形式。空白、大小写、全半角是
    排版差异；字符内容的增删仍会导致匹配失败，fabrication 依旧拦得住。
    """

    return re.sub(
        r"\s+",
        "",
        unicodedata.normalize("NFKC", str(text or "")).casefold(),
    )


def exact_atoms(text: str) -> list[str]:
    """返回必须逐字出现在证据中的原子片段，保持首次出现顺序。"""

    atoms: list[str] = []
    for match in EXACT_ATOM_RE.finditer(text):
        atom = next((item for item in match.groups() if item), "").strip()
        if atom:
            atoms.append(atom)
    for token in TECH_TOKEN_RE.findall(text):
        lowered = token.lower()
        if (
            any(character.isdigit() for character in token)
            or any(character in "-._:/\\" for character in token)
            or token != token.lower()
            or lowered in COMMAND_NAMES
        ):
            atoms.append(token)
    return list(dict.fromkeys(atoms))


def contains_command(text: str) -> bool:
    return bool(COMMAND_WORD_RE.search(text or ""))


__all__ = [
    "COMMAND_NAMES",
    "COMMAND_WORD_RE",
    "EXACT_ATOM_RE",
    "TECH_TOKEN_RE",
    "compact_text",
    "contains_command",
    "exact_atoms",
]
