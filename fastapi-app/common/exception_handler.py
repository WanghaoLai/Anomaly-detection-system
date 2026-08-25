import logging

from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from starlette import status
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


# 自定义异常类
class CustomException(Exception):
    def __init__(self, message: str):
        self.message = message


def setup_exceptions(app: FastAPI):
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": str(exc.status_code), "msg": exc.detail},
            headers=exc.headers,
        )

    @app.exception_handler(CustomException)
    async def custom_exception_handler(request: Request, exc: CustomException):
        return JSONResponse(
            status_code=status.HTTP_200_OK,  # http总是返回200
            content={"code": "500", "msg": exc.message}
        )

    @app.exception_handler(RequestValidationError)
    async def validate_exception_handler(request: Request, exc: RequestValidationError):
        # 422 的具体原因只进日志不返回，避免向客户端泄露内部字段细节。
        logger.warning(
            "请求参数校验失败: %s %s errors=%s",
            request.method,
            request.url.path,
            exc.errors(),
        )
        # 返回统一格式
        return JSONResponse(
            status_code=status.HTTP_200_OK,  # http总是返回200
            content={"code": "500", "msg": "请求参数错误"}
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        # 全量堆栈必须落日志：所有响应都是 200，这里是排障唯一线索。
        logger.exception(
            "未处理异常: %s %s", request.method, request.url.path
        )
        # 处理所有异常
        response = JSONResponse(
            status_code=status.HTTP_200_OK,  # http总是返回200
            content={"code": "500", "msg": "系统错误"}
        )
        return response
