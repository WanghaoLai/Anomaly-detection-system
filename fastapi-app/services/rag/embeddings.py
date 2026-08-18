"""兼容导入路径；真实实现已迁移，禁止在此新增业务逻辑。"""

from ._compat import reexport as _reexport
from .indexing import embedding as _implementation

_reexport(globals(), _implementation)
