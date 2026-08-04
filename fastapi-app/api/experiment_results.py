"""训练/推理实验图片的统一查询、预览与下载 API。"""

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.responses import Response, StreamingResponse

from common.auth import get_current_user
from common.result import PageInfo, Result
from services.experiment_result_service import (
    ExperimentResultError,
    experiment_result_service,
)


router = APIRouter(
    prefix="/experiment-results",
    dependencies=[Depends(get_current_user)],
)


def _source(value: str) -> str:
    normalized = value.strip().upper()
    if normalized not in {"TRAINING", "INFERENCE"}:
        raise HTTPException(status_code=400, detail="实验结果来源类型无效")
    return normalized


@router.get("/options")
async def options(current_user: dict = Depends(get_current_user)):
    return Result.success(await experiment_result_service.options(current_user))


@router.get("/runs")
async def runs(
    source_type: str = Query("", alias="sourceType"),
    algorithm_id: int | None = Query(None, alias="algorithmId", gt=0),
    dataset_id: int | None = Query(None, alias="datasetId", gt=0),
    page_num: int = Query(1, alias="pageNum", ge=1),
    page_size: int = Query(12, alias="pageSize", ge=1, le=50),
    current_user: dict = Depends(get_current_user),
):
    normalized = source_type.strip().upper()
    if normalized not in {"", "TRAINING", "INFERENCE"}:
        raise HTTPException(status_code=400, detail="实验结果来源类型无效")
    total, items = await experiment_result_service.list_runs(
        current_user,
        source_type=normalized,
        algorithm_id=algorithm_id,
        dataset_id=dataset_id,
        page_num=page_num,
        page_size=page_size,
    )
    return Result.success(PageInfo(total=total, list=items))


@router.get("/runs/{source_type}/{job_id}/images")
async def images(
    source_type: str,
    job_id: int,
    page_num: int = Query(1, alias="pageNum", ge=1),
    page_size: int = Query(24, alias="pageSize", ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    try:
        total, items = await experiment_result_service.list_images(
            _source(source_type), job_id, current_user, page_num, page_size
        )
    except ExperimentResultError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Result.success(PageInfo(total=total, list=items))


async def _image_response(
    source_type: str,
    job_id: int,
    image_key: str,
    current_user: dict,
    attachment: bool,
):
    try:
        content, media_type, name = await experiment_result_service.read_image(
            _source(source_type), job_id, image_key, current_user
        )
    except ExperimentResultError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    headers = {
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if attachment:
        headers["Content-Disposition"] = (
            f"attachment; filename*=UTF-8''{quote(name, safe='')}"
        )
    return Response(content=content, media_type=media_type, headers=headers)


@router.get("/runs/{source_type}/{job_id}/images/{image_key}/download")
async def download_image(
    source_type: str,
    job_id: int,
    image_key: str,
    current_user: dict = Depends(get_current_user),
):
    return await _image_response(
        source_type, job_id, image_key, current_user, attachment=True
    )


@router.get("/runs/{source_type}/{job_id}/images/{image_key}")
async def preview_image(
    source_type: str,
    job_id: int,
    image_key: str,
    current_user: dict = Depends(get_current_user),
):
    return await _image_response(
        source_type, job_id, image_key, current_user, attachment=False
    )


@router.get("/runs/{source_type}/{job_id}/download")
async def download_archive(
    source_type: str,
    job_id: int,
    current_user: dict = Depends(get_current_user),
):
    try:
        archive, name = await experiment_result_service.build_archive(
            _source(source_type), job_id, current_user
        )
    except ExperimentResultError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    archive.seek(0, 2)
    size = archive.tell()
    archive.seek(0)

    def stream():
        try:
            while True:
                chunk = archive.read(1024 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            archive.close()

    return StreamingResponse(
        stream(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name, safe='')}",
            "Content-Length": str(size),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
