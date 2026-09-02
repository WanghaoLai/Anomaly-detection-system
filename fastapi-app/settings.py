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


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: list[str]) -> list[str]:
    value = os.getenv(name)
    if not value:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


def _validate_jwt_secret(value: str | None) -> str:
    """校验 JWT 对称签名密钥，并在配置不安全时阻止应用启动。"""
    if value is None or not value.strip():
        raise RuntimeError(
            "JWT_SECRET_KEY 未配置；请使用密码学安全随机数生成器创建密钥"
        )

    secret = value.strip()
    insecure_values = {
        "change-me",
        "change-me-in-env",
        "changeme",
        "replace_with_a_long_random_secret",
        "secret",
    }
    if secret.lower() in insecure_values:
        raise RuntimeError("JWT_SECRET_KEY 仍为不安全的默认值或占位值")
    if len(secret.encode("utf-8")) < 32:
        raise RuntimeError("JWT_SECRET_KEY 至少需要 32 字节")
    return secret


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
    "dashscope_compatible_base_url": os.getenv(
        "DASHSCOPE_COMPATIBLE_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ).rstrip("/"),
    "model": os.getenv("DASHSCOPE_MODEL", "qwen-turbo"),
    "llm_timeout_seconds": _env_float("AI_LLM_TIMEOUT_SECONDS", 45.0),
    "llm_max_retries": _env_int("AI_LLM_MAX_RETRIES", 2),
    "llm_retry_backoff_seconds": _env_float("AI_LLM_RETRY_BACKOFF_SECONDS", 0.4),
    "llm_circuit_failure_threshold": _env_int(
        "AI_LLM_CIRCUIT_FAILURE_THRESHOLD", 5
    ),
    "llm_circuit_recovery_seconds": _env_float(
        "AI_LLM_CIRCUIT_RECOVERY_SECONDS", 30.0
    ),
    "embedding_model": os.getenv("DASHSCOPE_EMBEDDING_MODEL", "text-embedding-v2"),
    # 个人开发迁移默认仍使用 Chroma；Qdrant 仅在显式配置时启用。
    "vector_store_provider": os.getenv(
        "AI_VECTOR_STORE_PROVIDER", "chroma"
    ).strip().lower(),
    "qdrant_mode": os.getenv("AI_QDRANT_MODE", "local").strip().lower(),
    "qdrant_path": os.getenv("AI_QDRANT_PATH", "") or str(
        BASE_DIR / "qdrant_db"
    ),
    "qdrant_url": os.getenv(
        "AI_QDRANT_URL", "http://127.0.0.1:6333"
    ).rstrip("/"),
    "qdrant_api_key": os.getenv("AI_QDRANT_API_KEY", ""),
    "qdrant_timeout_seconds": _env_float("AI_QDRANT_TIMEOUT_SECONDS", 10.0),
    "qdrant_prefer_grpc": _env_bool("AI_QDRANT_PREFER_GRPC", False),
    "qdrant_batch_size": _env_int("AI_QDRANT_BATCH_SIZE", 100),
    "max_history": _env_int("AI_MAX_HISTORY", 20),
    "top_k": _env_int("AI_TOP_K", 3),
    "rag_candidate_k": _env_int("AI_RAG_CANDIDATE_K", 8),
    "rag_dense_candidate_k": _env_int("AI_RAG_DENSE_CANDIDATE_K", 50),
    "rag_lexical_candidate_k": _env_int("AI_RAG_LEXICAL_CANDIDATE_K", 50),
    "rag_candidate_union_limit": _env_int("AI_RAG_CANDIDATE_UNION_LIMIT", 100),
    "rag_final_k": _env_int("AI_RAG_FINAL_K", 4),
    "rag_rerank_input_k": _env_int("AI_RAG_RERANK_INPUT_K", 50),
    "rag_rerank_final_k": _env_int("AI_RAG_RERANK_FINAL_K", 8),
    "rag_score_threshold": _env_float("AI_RAG_SCORE_THRESHOLD", 0.20),
    "rag_hybrid_enabled": _env_bool("AI_RAG_HYBRID_ENABLED", True),
    "rag_acl_pushdown_enabled": _env_bool("AI_RAG_ACL_PUSHDOWN_ENABLED", True),
    "rag_bm25_enabled": _env_bool("AI_RAG_BM25_ENABLED", True),
    "rag_reranker_enabled": _env_bool("AI_RAG_RERANKER_ENABLED", False),
    "rag_reranker_model": os.getenv("AI_RAG_RERANKER_MODEL", ""),
    "rag_reranker_timeout_seconds": _env_float(
        "AI_RAG_RERANKER_TIMEOUT_SECONDS", 2.0
    ),
    "rag_reranker_max_length": _env_int("AI_RAG_RERANKER_MAX_LENGTH", 0),
    "rag_audit_enabled": _env_bool("AI_RAG_AUDIT_ENABLED", True),
    # Phase 5：覆盖路由、改写、检索、生成和校验的整条请求截止时间。
    "rag_request_deadline_seconds": _env_float(
        "AI_RAG_REQUEST_DEADLINE_SECONDS", 75.0
    ),
    "rag_lexical_min_score": _env_float("AI_RAG_LEXICAL_MIN_SCORE", 0.08),
    "rag_context_tokens": _env_int("AI_RAG_CONTEXT_TOKENS", 2800),
    # P4 Context Packing：标题、来源、Node ID、正文和省略标记共同计入总预算。
    # 普通 Node 使用软上限；原子命令允许借用软上限但不得突破总预算。
    "rag_context_min_node_tokens": _env_int(
        "AI_RAG_CONTEXT_MIN_NODE_TOKENS", 48
    ),
    "rag_context_max_node_tokens": _env_int(
        "AI_RAG_CONTEXT_MAX_NODE_TOKENS", 420
    ),
    "rag_context_duplicate_similarity": _env_float(
        "AI_RAG_CONTEXT_DUPLICATE_SIMILARITY", 0.92
    ),
    "rag_context_precision_target": _env_float(
        "AI_RAG_CONTEXT_PRECISION_TARGET", 0.70
    ),
    "rag_faithfulness_threshold": _env_float(
        "AI_RAG_FAITHFULNESS_THRESHOLD", 0.90
    ),
    # claim 级词面支持下限：纯文字 claim（无命令/路径/数值原子可校验）至少
    # 要有三成加权特征命中证据，拦截"服务器每天自动重启"式的编造。
    "rag_claim_lexical_support": _env_float(
        "AI_RAG_CLAIM_LEXICAL_SUPPORT", 0.30
    ),
    # JSON Object 仅保证返回对象；允许一次受控重生成修复字段缺失。
    "rag_grounding_validation_retries": _env_int(
        "AI_RAG_GROUNDING_VALIDATION_RETRIES", 1
    ),
    "rag_query_history_turns": _env_int("AI_RAG_QUERY_HISTORY_TURNS", 2),
    # Phase 3 先离线评测，生产 Router/Classifier/Rewrite 必须由人工再次确认后开启。
    "rag_phase3_router_enabled": _env_bool(
        "AI_RAG_PHASE3_ROUTER_ENABLED", False
    ),
    "rag_phase3_rewrite_enabled": _env_bool(
        "AI_RAG_PHASE3_REWRITE_ENABLED", False
    ),
    "rag_phase3_classifier_confidence": _env_float(
        "AI_RAG_PHASE3_CLASSIFIER_CONFIDENCE", 0.75
    ),
    "rag_phase3_classifier_timeout_seconds": _env_float(
        "AI_RAG_PHASE3_CLASSIFIER_TIMEOUT_SECONDS", 8.0
    ),
    "rag_phase3_rewrite_timeout_seconds": _env_float(
        "AI_RAG_PHASE3_REWRITE_TIMEOUT_SECONDS", 8.0
    ),
    "rag_chunk_tokens": _env_int("AI_RAG_CHUNK_TOKENS", 500),
    "rag_overlap_tokens": _env_int("AI_RAG_OVERLAP_TOKENS", 50),
    "rag_embedding_cache_enabled": _env_bool(
        "AI_RAG_EMBEDDING_CACHE_ENABLED", True
    ),
    "rag_embedding_cache_path": os.getenv("AI_RAG_EMBEDDING_CACHE_PATH", ""),
    # Phase 4B 默认关闭；完成本地 OCR 路由门禁后再由人工启用。
    "rag_ocr_enabled": _env_bool("AI_RAG_OCR_ENABLED", False),
    "rag_ocr_tesseract_path": os.getenv("AI_RAG_OCR_TESSERACT_PATH", ""),
    "rag_ocr_pdftoppm_path": os.getenv("AI_RAG_OCR_PDFTOPPM_PATH", ""),
    "rag_ocr_pdfinfo_path": os.getenv("AI_RAG_OCR_PDFINFO_PATH", ""),
    "rag_ocr_tessdata_path": os.getenv("AI_RAG_OCR_TESSDATA_PATH", ""),
    "rag_ocr_languages": os.getenv("AI_RAG_OCR_LANGUAGES", "chi_sim+eng"),
    "rag_ocr_dpi": _env_int("AI_RAG_OCR_DPI", 300),
    "rag_ocr_timeout_seconds": _env_float(
        "AI_RAG_OCR_TIMEOUT_SECONDS", 120.0
    ),
    "rag_ocr_min_chars": _env_int("AI_RAG_OCR_MIN_CHARS", 200),
    "rag_ocr_min_chars_per_page": _env_int(
        "AI_RAG_OCR_MIN_CHARS_PER_PAGE", 20
    ),
    # Phase 4C 离线通过后才在生产环境启用发布硬门禁。
    "rag_release_smoke_required": _env_bool(
        "AI_RAG_RELEASE_SMOKE_REQUIRED", False
    ),
    "rag_release_smoke_set_path": os.getenv(
        "AI_RAG_RELEASE_SMOKE_SET_PATH"
    ) or str(BASE_DIR.parent / "config" / "rag_phase4_release_smoke_v0.json"),
    "rag_max_upload_bytes": _env_int("AI_RAG_MAX_UPLOAD_BYTES", 20 * 1024 * 1024),
    # Phase 6：解析前上传安全门禁；人工已确认生产 Fail Closed 策略。
    "rag_upload_security_enabled": _env_bool("AI_RAG_UPLOAD_SECURITY_ENABLED", True),
    "rag_archive_max_entries": _env_int("AI_RAG_ARCHIVE_MAX_ENTRIES", 5000),
    "rag_archive_max_uncompressed_bytes": _env_int(
        "AI_RAG_ARCHIVE_MAX_UNCOMPRESSED_BYTES", 200 * 1024 * 1024
    ),
    "rag_archive_max_compression_ratio": _env_float(
        "AI_RAG_ARCHIVE_MAX_COMPRESSION_RATIO", 100.0
    ),
    "rag_malware_scan_enabled": _env_bool("AI_RAG_MALWARE_SCAN_ENABLED", True),
    "rag_clamav_path": os.getenv(
        "AI_RAG_CLAMAV_PATH", "/usr/local/clamav/bin/clamscan"
    ),
    "rag_clamav_version": os.getenv("AI_RAG_CLAMAV_VERSION", "1.5.4"),
    "rag_clamav_database_path": os.getenv(
        "AI_RAG_CLAMAV_DATABASE_PATH", "/usr/local/clamav/share/clamav"
    ),
    "rag_clamav_certs_path": os.getenv(
        "AI_RAG_CLAMAV_CERTS_PATH", "/usr/local/clamav/etc/certs"
    ),
    "rag_clamav_timeout_seconds": _env_float(
        "AI_RAG_CLAMAV_TIMEOUT_SECONDS", 120.0
    ),
    "rag_clamav_max_signature_age_seconds": _env_float(
        "AI_RAG_CLAMAV_MAX_SIGNATURE_AGE_SECONDS", 86400.0
    ),
    "rag_parser_isolation_enabled": _env_bool(
        "AI_RAG_PARSER_ISOLATION_ENABLED", True
    ),
    "rag_parser_wall_timeout_seconds": _env_float(
        "AI_RAG_PARSER_WALL_TIMEOUT_SECONDS", 120.0
    ),
    "rag_parser_memory_limit_bytes": _env_int(
        "AI_RAG_PARSER_MEMORY_LIMIT_BYTES", 1024 * 1024 * 1024
    ),
    "rag_parser_cpu_limit_seconds": _env_int(
        "AI_RAG_PARSER_CPU_LIMIT_SECONDS", 60
    ),
    # P1 原始文件、DocStore 和发布清单的本地持久化根目录。
    # 该目录与 Chroma 分离：前者是可追溯事实源，后者是可重建派生物。
    "rag_artifact_path": os.getenv(
        "AI_RAG_ARTIFACT_PATH"
    ) or str(BASE_DIR / "files" / "knowledge"),
    # DashScope 当前 text-embedding-v2 单批最多 25 条；v3/v4 会由服务层自动
    # 收紧到接口上限。重试只覆盖网络、限流和 5xx 等临时错误。
    "embedding_batch_size": _env_int("AI_EMBEDDING_BATCH_SIZE", 25),
    "embedding_max_retries": _env_int("AI_EMBEDDING_MAX_RETRIES", 3),
    "embedding_retry_backoff_seconds": _env_float(
        "AI_EMBEDDING_RETRY_BACKOFF_SECONDS", 0.5
    ),
    # 影子索引是全量写入，默认串行以避免同机 embedding/磁盘争用。
    "rag_ingestion_concurrency": _env_int("AI_RAG_INGESTION_CONCURRENCY", 1),
}

# JWT 配置
JWT_SECRET_KEY = _validate_jwt_secret(os.getenv("JWT_SECRET_KEY"))
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_ACCESS_EXPIRE_MINUTES = _env_int("JWT_ACCESS_EXPIRE_MINUTES", 15)
JWT_REFRESH_EXPIRE_DAYS = _env_int("JWT_REFRESH_EXPIRE_DAYS", 7)
JWT_COOKIE_SECURE = _env_bool("JWT_COOKIE_SECURE", False)
JWT_COOKIE_SAMESITE = os.getenv("JWT_COOKIE_SAMESITE", "lax").lower()
if JWT_COOKIE_SAMESITE not in {"lax", "strict", "none"}:
    raise RuntimeError("JWT_COOKIE_SAMESITE 必须为 lax、strict 或 none")
if JWT_COOKIE_SAMESITE == "none" and not JWT_COOKIE_SECURE:
    raise RuntimeError("SameSite=None 的认证 Cookie 必须启用 Secure")

ACCESS_COOKIE_NAME = "access_token"
REFRESH_COOKIE_NAME = "refresh_token"
CSRF_COOKIE_NAME = "csrf_token"

# 登录限流
LOGIN_RATE_LIMIT_ATTEMPTS = _env_int("LOGIN_RATE_LIMIT_ATTEMPTS", 5)
LOGIN_RATE_LIMIT_WINDOW_SECONDS = _env_int("LOGIN_RATE_LIMIT_WINDOW_SECONDS", 300)
LOGIN_RATE_LIMIT_LOCK_SECONDS = _env_int("LOGIN_RATE_LIMIT_LOCK_SECONDS", 900)

# 带凭据的 CORS 不能使用 "*"，生产环境应显式配置真实前端域名。
CORS_ALLOWED_ORIGINS = _env_list(
    "CORS_ALLOWED_ORIGINS",
    ["http://localhost:5173", "http://127.0.0.1:5173"],
)

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
    "conda_env_roots_json": os.getenv(
        "GPU_CONDA_ENV_ROOTS_JSON",
        '{"系统 Conda 环境":"/opt/conda/envs"}',
    ),
    "conda_env_max_entries": _env_int("GPU_CONDA_ENV_MAX_ENTRIES", 500),
}

# 训练执行器使用独立低权限账号；运行目标只能来自管理员白名单。
TRAINING_EXECUTOR_CONFIG = {
    "enabled": _env_bool("TRAINING_EXECUTOR_ENABLED", False),
    "host": os.getenv("TRAINING_SERVER_HOST", os.getenv("GPU_SERVER_HOST", "")),
    "port": _env_int(
        "TRAINING_SERVER_PORT",
        _env_int("GPU_SERVER_PORT", 22),
    ),
    "ssh_user": os.getenv("TRAINING_SERVER_SSH_USER", "adtrainer"),
    "private_key_path": os.getenv(
        "TRAINING_SERVER_PRIVATE_KEY_PATH",
        os.getenv("GPU_SERVER_PRIVATE_KEY_PATH", ""),
    ),
    "known_hosts_path": os.getenv(
        "TRAINING_SERVER_KNOWN_HOSTS_PATH",
        os.getenv("GPU_SERVER_KNOWN_HOSTS_PATH", ""),
    ),
    "connect_timeout": _env_float("TRAINING_CONNECT_TIMEOUT", 8.0),
    "command_timeout": _env_float("TRAINING_COMMAND_TIMEOUT", 20.0),
    "monitor_interval": _env_float("TRAINING_MONITOR_INTERVAL_SECONDS", 5.0),
    "max_pending_jobs_per_user": _env_int(
        "TRAINING_MAX_PENDING_JOBS_PER_USER",
        3,
    ),
    "max_concurrent_jobs": _env_int(
        "TRAINING_MAX_CONCURRENT_JOBS",
        4,
    ),
    "max_runtime_seconds": _env_int(
        "TRAINING_MAX_RUNTIME_SECONDS",
        21600,
    ),
    "artifact_retention_days": _env_int(
        "TRAINING_ARTIFACT_RETENTION_DAYS",
        30,
    ),
    "min_free_gpu_memory_mb": _env_int(
        "TRAINING_MIN_FREE_GPU_MEMORY_MB",
        8000,
    ),
    "control_root": os.getenv(
        "TRAINING_REMOTE_CONTROL_ROOT",
        "/home/adtrainer/training-control",
    ),
    "output_root": os.getenv(
        "TRAINING_REMOTE_OUTPUT_ROOT",
        "/home/adtrainer/training-runs",
    ),
    "runner_path": os.getenv(
        "TRAINING_REMOTE_RUNNER_PATH",
        "/home/adtrainer/bin/phase0_pbas_runner_dataset_v2.py",
    ),
    "gpu_allowlist": [
        int(item)
        for item in _env_list("TRAINING_GPU_ALLOWLIST", ["0", "1", "2", "3"])
    ],
}

# 推理复用训练服务器与低权限账号，但拥有独立 runner 和输出根目录。
INFERENCE_EXECUTOR_CONFIG = {
    **TRAINING_EXECUTOR_CONFIG,
    "runner_path": os.getenv(
        "INFERENCE_REMOTE_RUNNER_PATH",
        "/home/adtrainer/bin/phase0_pbas_inference_runner.py",
    ),
    "control_root": os.getenv(
        "INFERENCE_REMOTE_CONTROL_ROOT",
        "/home/adtrainer/inference-control",
    ),
    "output_root": os.getenv(
        "INFERENCE_REMOTE_OUTPUT_ROOT",
        "/home/adtrainer/inference-runs",
    ),
    "max_concurrent_jobs": _env_int("INFERENCE_MAX_CONCURRENT_JOBS", 2),
    "max_pending_jobs_per_user": _env_int("INFERENCE_MAX_PENDING_JOBS_PER_USER", 3),
    "max_runtime_seconds": _env_int("INFERENCE_MAX_RUNTIME_SECONDS", 1800),
}
