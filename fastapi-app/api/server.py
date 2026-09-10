from common.auth import get_current_admin, get_current_user
from common.result import Result
from fastapi import APIRouter, Depends, HTTPException, Query
from services.gpu_server_service import GpuServerError, gpu_server_service

router = APIRouter(prefix="/server", dependencies=[Depends(get_current_user)])


@router.get("/summary")
async def get_server_summary(refresh: bool = False):
    try:
        return Result.success(await gpu_server_service.get_summary(force=refresh))
    except GpuServerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/files")
async def get_account_files(
    root_id: str = "",
    path: str = "",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    try:
        data = await gpu_server_service.get_files(
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
async def get_account_file_roots(current_user: dict = Depends(get_current_user)):
    try:
        return Result.success(
            gpu_server_service.get_file_roots(current_user["username"])
        )
    except GpuServerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/conda-environments", dependencies=[Depends(get_current_admin)])
async def get_conda_environments():
    try:
        return Result.success(await gpu_server_service.get_conda_environments())
    except GpuServerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
