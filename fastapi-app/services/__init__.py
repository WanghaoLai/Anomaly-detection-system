"""服务包的惰性公共导出。

训练执行器不应因为导入算法插件而强制加载 LLM、向量库等无关依赖。
"""

from importlib import import_module
from typing import TYPE_CHECKING


__all__ = ["LLMService", "ChatService", "KnowledgeService"]

_EXPORTS = {
    "LLMService": (".llm_service", "LLMService"),
    "ChatService": (".chat_service", "ChatService"),
    "KnowledgeService": (".knowledge_service", "KnowledgeService"),
}

if TYPE_CHECKING:
    from .chat_service import ChatService
    from .knowledge_service import KnowledgeService
    from .llm_service import LLMService


def __getattr__(name: str):
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value
