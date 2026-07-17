import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(BASE_DIR / ".env")


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


TORTOISE_ORM = {
    "connections": {
        "default": {
            "engine": "tortoise.backends.mysql",
            "credentials": {
                "host": os.getenv("MYSQL_HOST", "localhost"),
                "port": _env_int("MYSQL_PORT", 3306),
                "database": os.getenv("MYSQL_DATABASE", "ad_system"),
                "user": os.getenv("MYSQL_USER", "root"),
                "password": os.getenv("MYSQL_PASSWORD", ""),
                "minsize": _env_int("MYSQL_POOL_MINSIZE", 1),
                "maxsize": _env_int("MYSQL_POOL_MAXSIZE", 10),
                "charset": os.getenv("MYSQL_CHARSET", "utf8mb4"),
                "echo": os.getenv("MYSQL_ECHO", "false").lower() == "true"
            }
        },
    },
    "apps": {
      "models": {
          "models": ["models"],
          "default_connection": "default",
      }
    },
    "use_tz": True,  # 是否使用时区
    "timezone": "Asia/Shanghai"
}

# 智能问答配置
AI_CONFIG = {
    "dashscope_api_key": os.getenv("DASHSCOPE_API_KEY", ""),
    "model": os.getenv("DASHSCOPE_MODEL", "qwen-turbo"),
    "embedding_model": os.getenv("DASHSCOPE_EMBEDDING_MODEL", "text-embedding-v2"),
    "max_history": _env_int("AI_MAX_HISTORY", 20),
    "top_k": _env_int("AI_TOP_K", 3),
}

# JWT 配置
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "change-me-in-env")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_HOURS = _env_int("JWT_EXPIRE_HOURS", 24)

