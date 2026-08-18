"""加载与切分共享的 Markdown 语法定义，不依赖任何解析厂商。"""

from __future__ import annotations

import re


MARKDOWN_HEADING_RE = re.compile(
    r"^ {0,3}(?P<marks>#{1,6})(?:[ \t]+(?P<title>.*?)\s*|[ \t]*)$"
)
MARKDOWN_FENCE_RE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})")


__all__ = ["MARKDOWN_FENCE_RE", "MARKDOWN_HEADING_RE"]
