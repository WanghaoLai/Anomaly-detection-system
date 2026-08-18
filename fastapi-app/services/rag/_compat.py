"""旧模块路径的集中兼容机制。

兼容模块只调用这里的函数，不允许复制真实实现。删除兼容层时可通过一个入口统一
加入告警、遥测或阻断策略。
"""

from __future__ import annotations

from types import ModuleType
from typing import MutableMapping


def reexport(namespace: MutableMapping[str, object], implementation: ModuleType) -> None:
    """把目标模块符号以同一对象身份暴露到旧模块命名空间。"""

    for name, value in vars(implementation).items():
        if not name.startswith("__"):
            namespace[name] = value
    declared = getattr(implementation, "__all__", None)
    namespace["__all__"] = tuple(
        declared
        if declared is not None
        else (name for name in vars(implementation) if not name.startswith("_"))
    )
    namespace["_implementation_module"] = implementation


__all__ = ["reexport"]
