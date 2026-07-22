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


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value else default


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

# 远程 GPU 服务器配置（凭据仅在后端使用）
GPU_SERVER_CONFIG = {
    "host": os.getenv("GPU_SERVER_HOST", ""),
    "port": _env_int("GPU_SERVER_PORT", 22),
    "ssh_user": os.getenv("GPU_SERVER_SSH_USER", ""),
    "ssh_password": os.getenv("GPU_SERVER_SSH_PASSWORD", ""),
    "private_key_path": os.getenv("GPU_SERVER_PRIVATE_KEY_PATH", ""),
    "known_hosts_path": os.getenv("GPU_SERVER_KNOWN_HOSTS_PATH", ""),
    "connect_timeout": _env_float("GPU_SERVER_CONNECT_TIMEOUT", 5.0),
    "command_timeout": _env_float("GPU_SERVER_COMMAND_TIMEOUT", 8.0),
    "status_cache_seconds": _env_float("GPU_STATUS_CACHE_SECONDS", 5.0),
    "expected_gpu_count": _env_int("GPU_EXPECTED_COUNT", 4),
    "account_root_template": os.getenv(
        "GPU_ACCOUNT_ROOT_TEMPLATE", "/home/{username}"
    ),
    "account_map_json": os.getenv("GPU_ACCOUNT_MAP_JSON", "{}"),
    "allowed_directories_json": os.getenv(
        "GPU_ACCOUNT_ALLOWED_DIRECTORIES_JSON", ""
    ),
    "file_max_entries": _env_int("GPU_FILE_MAX_ENTRIES", 5000),
}

