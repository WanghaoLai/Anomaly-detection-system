import asyncio
from contextlib import asynccontextmanager, suppress
import time

from fastapi import FastAPI
import logging
import uvicorn
from starlette.middleware.cors import CORSMiddleware
from tortoise.contrib.fastapi import register_tortoise

from api import api_router
from api.admin_chat import _llm_service
from api.chat import llm_service
from api.knowledge import recover_pending_knowledge_releases
from common.auth import validate_password_storage
from common.exception_handler import setup_exceptions
from common.migrations import check_schema_current

from common.result import Result
from settings import API_PREFIX, CORS_ALLOWED_ORIGINS, DB_SCHEMA_CHECK_ENABLED, TORTOISE_ORM
from services.knowledge_service import knowledge_service
from services.training_executor_service import training_executor_service
from services.inference_executor_service import inference_executor_service

logger = logging.getLogger(__name__)


def _validate_rag_startup_state() -> None:
    """在启动阶段尽力校验当前向量索引。

    这个检查只读且不应该成为整个平台的单点故障：Qdrant Cloud
    短暂超时或代理返回 503 时，用户、训练、推理和 GPU 监控仍应该可用。
    真正存在 PENDING 发布时的恢复仍由
    ``recover_pending_knowledge_releases`` 在此之前执行并 fail closed。
    """
    started_at = time.perf_counter()
    try:
        try:
            report = knowledge_service.validate_embedding_config()
        except Exception:
            logger.exception(
                "RAG 向量库启动检查不可用，知识库将暂时降级，"
                "其余系统功能继续启动"
            )
            return
        if not report["consistent"]:
            logger.warning(
                "RAG embedding 配置不一致，新增文档与检索将被拒绝/降级：\n  %s",
                "\n  ".join(report["issues"]),
            )
    finally:
        logger.info(
            "RAG 向量库后台启动检查已完成，耗时 %.2f 秒",
            time.perf_counter() - started_at,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    if DB_SCHEMA_CHECK_ENABLED:
        # 在任何后台任务启动前 fail closed，禁止新代码运行在旧表结构上。
        await check_schema_current()
    legacy_password_count = await validate_password_storage()
    if legacy_password_count:
        logger.warning(
            "检测到 %s 个遗留明文密码账号；当前兼容窗口仍开启，"
            "请尽快运行 migrate_passwords.py --apply 后关闭兼容",
            legacy_password_count,
        )
    # 元数据事务已提交但指针尚未切换的 release 必须先恢复，避免用户看到
    # MySQL 与实际检索版本不一致的知识库。
    await recover_pending_knowledge_releases()
    await training_executor_service.start_monitor()
    await inference_executor_service.start_monitor()
    # 远程 Qdrant 只读检查在网络不佳时可能等待数秒。它只用于
    # 提前记录配置警告，不应阻塞健康接口与其他完整功能就绪。
    rag_validation_task = asyncio.create_task(
        asyncio.to_thread(_validate_rag_startup_state),
        name="rag-startup-validation",
    )
    yield
    if not rag_validation_task.done():
        rag_validation_task.cancel()
    with suppress(asyncio.CancelledError):
        await rag_validation_task
    # API 单例复用的 Qwen HTTP 连接池在应用退出时显式关闭。
    await llm_service.aclose()
    await _llm_service.aclose()
    await inference_executor_service.stop_monitor()
    await training_executor_service.stop_monitor()


app = FastAPI(
    lifespan=lifespan,
    openapi_url=f"{API_PREFIX}/openapi.json",
    docs_url=f"{API_PREFIX}/docs",
    redoc_url=f"{API_PREFIX}/redoc",
    swagger_ui_oauth2_redirect_url=f"{API_PREFIX}/docs/oauth2-redirect",
)

# 跨域配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],  # 允许所有 HTTP 方法
    allow_headers=["*"],  # 允许所有请求头
)

# 配置路由
app.include_router(api_router, prefix=API_PREFIX)
# 注册orm
register_tortoise(app, config=TORTOISE_ORM, add_exception_handlers=True)
# 注册异常处理器
setup_exceptions(app)


@app.get(f"{API_PREFIX}/health")
async def root():
    return Result.success()

if __name__ == "__main__":
    uvicorn.run("main:app", reload=True, port=9090, reload_dirs=["api", "common", "services"])

