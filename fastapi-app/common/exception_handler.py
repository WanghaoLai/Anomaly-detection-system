import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from starlette import status
from starlette.responses import JSONResponse


logger = logging.getLogger(__name__)


class CustomException(Exception):
    """可安全展示给调用方的业务异常。"""

    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


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
            status_code=exc.status_code,
            content={"code": str(exc.status_code), "msg": exc.message},
        )

    @app.exception_handler(RequestValidationError)
    async def validate_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ):
        # 具体校验细节只进日志，避免向客户端泄露内部字段结构。
        # errors() 的 input/ctx 携带完整原始请求体（含密码等敏感字段），
        # 同样不能落日志，否则日志会成为明文口令副本。
        safe_errors = [
            {key: value for key, value in error.items() if key not in ("input", "ctx")}
            for error in exc.errors()
        ]
        logger.warning(
            "请求参数校验失败: %s %s errors=%s",
            request.method,
            request.url.path,
            safe_errors,
        )
        return JSONResponse(
            # Starlette 0.47 仍使用 UNPROCESSABLE_ENTITY 这一常量名。
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"code": "422", "msg": "请求参数错误"},
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        # 全量堆栈落日志，响应只暴露稳定的通用错误信息。
        logger.exception(
            "未处理异常: %s %s",
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"code": "500", "msg": "系统错误"},
        )
