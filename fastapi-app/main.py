from contextlib import asynccontextmanager

from fastapi import FastAPI
import logging
import uvicorn
from starlette.middleware.cors import CORSMiddleware
from tortoise.contrib.fastapi import register_tortoise

from api import api_router
from api.admin_chat import _llm_service
from api.chat import llm_service
from common.exception_handler import setup_exceptions

from common.result import Result
from settings import CORS_ALLOWED_ORIGINS, TORTOISE_ORM
from services.knowledge_service import knowledge_service
from services.training_executor_service import training_executor_service
from services.inference_executor_service import inference_executor_service

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    report = knowledge_service.validate_embedding_config()
    if not report["consistent"]:
        logger.warning(
            "RAG embedding 配置不一致，新增文档与检索将被拒绝/降级：\n  %s",
            "\n  ".join(report["issues"]),
        )
    await training_executor_service.start_monitor()
    await inference_executor_service.start_monitor()
    yield
    # API 单例复用的 Qwen HTTP 连接池在应用退出时显式关闭。
    await llm_service.aclose()
    await _llm_service.aclose()
    await inference_executor_service.stop_monitor()
    await training_executor_service.stop_monitor()


app = FastAPI(lifespan=lifespan)

# 跨域配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],  # 允许所有 HTTP 方法
    allow_headers=["*"],  # 允许所有请求头
)

# 配置路由
app.include_router(api_router)
# 注册orm
register_tortoise(app, config=TORTOISE_ORM, add_exception_handlers=True)
# 注册异常处理器
setup_exceptions(app)


@app.get("/")
async def root():
    return Result.success()

if __name__ == "__main__":
    uvicorn.run("main:app", reload=True, port=9090, reload_dirs=["api", "common", "services"])

