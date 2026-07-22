from fastapi import APIRouter, Depends, Query

from common.auth import get_current_user
from common.result import Result
from services.gpu_server_service import GpuServerError, gpu_server_service


router = APIRouter(prefix="/server", dependencies=[Depends(get_current_user)])


@router.get("/summary")
async def get_server_summary(refresh: bool = False):
    return Result.success(await gpu_server_service.get_summary(force=refresh))


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
        return Result.error(str(exc))


@router.get("/file-roots")
async def get_account_file_roots(current_user: dict = Depends(get_current_user)):
    try:
        return Result.success(
            gpu_server_service.get_file_roots(current_user["username"])
        )
    except GpuServerError as exc:
        return Result.error(str(exc))
