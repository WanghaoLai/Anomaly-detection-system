from common.auth import get_current_admin, get_current_user
from common.result import Result
from fastapi import APIRouter, Depends, HTTPException, Query
from services.gpu_server_service import (
    GpuServerError,
    gpu_server_configuration_status,
    gpu_server_registry,
)

router = APIRouter(prefix="/server", dependencies=[Depends(get_current_user)])


def _selected_service(server_id: str):
    try:
        return gpu_server_registry.get(server_id)
    except GpuServerError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/servers")
async def get_servers():
    """返回可选服务器的非敏感信息，不暴露 SSH 凭据或本地密钥路径。"""
    return Result.success(gpu_server_registry.public_options())


@router.get(
    "/configuration-health",
    dependencies=[Depends(get_current_admin)],
)
async def get_configuration_health():
    """供管理员和部署探针识别追加服务器配置降级。"""
    return Result.success(gpu_server_configuration_status)


@router.get("/summary")
async def get_server_summary(
    refresh: bool = False,
    server_id: str = Query(default="", alias="serverId", max_length=32),
):
    try:
        service = _selected_service(server_id)
        return Result.success(await service.get_summary(force=refresh))
    except GpuServerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/files")
async def get_account_files(
    root_id: str = "",
    path: str = "",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    server_id: str = Query(default="", alias="serverId", max_length=32),
    current_user: dict = Depends(get_current_user),
):
    try:
        service = _selected_service(server_id)
        data = await service.get_files(
            app_username=current_user["username"],
            root_id=root_id,
            relative_path=path,
            page=page,
            page_size=page_size,
        )
        return Result.success(data)
    except GpuServerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/file-roots")
async def get_account_file_roots(
    server_id: str = Query(default="", alias="serverId", max_length=32),
    current_user: dict = Depends(get_current_user),
):
    try:
        service = _selected_service(server_id)
        return Result.success(
            service.get_file_roots(current_user["username"])
        )
    except GpuServerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/conda-environments", dependencies=[Depends(get_current_admin)])
async def get_conda_environments(
    server_id: str = Query(default="", alias="serverId", max_length=32),
):
    try:
        service = _selected_service(server_id)
        return Result.success(await service.get_conda_environments())
    except GpuServerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
