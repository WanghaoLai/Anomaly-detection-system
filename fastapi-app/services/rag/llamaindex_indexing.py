"""兼容模块别名；真实实现位于新分层目录。"""

import sys as _sys
from .indexing import writer as _implementation

_sys.modules[__name__] = _implementation
