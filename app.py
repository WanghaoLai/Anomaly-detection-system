"""Vercel FastAPI 入口。"""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent / "fastapi-app"

# 兼容后端现有的 from api、from common、from settings 导入方式。
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from main import app

__all__ = ["app"]