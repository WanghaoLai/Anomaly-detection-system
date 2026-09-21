"""由已训练模型驱动的通用算法推理 API。"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from common.auth import get_current_user
from common.result import PageInfo, Result
from models import Algorithm, Dataset, InferenceJob, TrainingJob
from services.inference_executor_service import (
    InferenceExecutorError,
    inference_executor_service,
)
from services.algorithm_adapters import algorithm_adapter_registry
from services.gpu_server_service import GpuServerError, gpu_server_registry


router = APIRouter(
    prefix="/inference",
    dependencies=[Depends(get_current_user)],
)
logger = logging.getLogger(__name__)


class InferenceJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    training_job_id: int = Field(alias="trainingJobId", gt=0)
    classes: list[str] = Field(default_factory=list)
    requested_gpu: int | None = Field(default=None, alias="requestedGpu", ge=0)


def _data(job: InferenceJob, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        server = gpu_server_registry.public_identity(job.server_id)
    except GpuServerError:
        server = {
            "server_id": job.server_id,
            "server_name": "未配置的服务器",
            "server_host": "--",
        }
    return {
        "id": job.id,
        "jobNo": job.job_no,
        "trainingJobId": job.training_job_id,
        "serverId": server["server_id"],
        "serverName": server["server_name"],
        "serverHost": server["server_host"],
        "status": job.status,
        "config": job.config_json,
        "result": job.result_json,
        "assignedGpu": job.assigned_gpu,
        "exitCode": job.exit_code,
        "failureReason": job.failure_reason,
        "submittedAt": job.submitted_at,
        "startedAt": job.started_at,
        "finishedAt": job.finished_at,
        **(metadata or {}),
    }


async def _metadata(jobs: list[InferenceJob]) -> dict[int, dict[str, Any]]:
    source_ids = {job.training_job_id for job in jobs}
    sources = await TrainingJob.filter(id__in=source_ids) if source_ids else []
    source_map = {source.id: source for source in sources}
    algorithm_ids = {source.algorithm_id for source in sources}
    dataset_ids = {source.dataset_id for source in sources}
    algorithms = {
        item.id: item for item in await Algorithm.filter(id__in=algorithm_ids)
    } if algorithm_ids else {}
    datasets = {
        item.id: item for item in await Dataset.filter(id__in=dataset_ids)
    } if dataset_ids else {}
    result = {}
    for job in jobs:
        source = source_map.get(job.training_job_id)
        if source is None:
            continue
        algorithm = algorithms.get(source.algorithm_id)
        dataset = datasets.get(source.dataset_id)
        result[job.id] = {
            "trainingJobNo": source.job_no,
            "algorithmId": source.algorithm_id,
            "algorithmName": algorithm.name if algorithm else None,
            "algorithmAbbreviation": algorithm.abbreviation if algorithm else None,
            "datasetId": source.dataset_id,
            "datasetName": dataset.name if dataset else None,
            "serverId": source.server_id,
        }
    return result


async def _accessible(job_id: int, current_user: dict) -> InferenceJob:
    job = await InferenceJob.get_or_none(id=job_id)
    if job is None or (
        current_user["role"] != "管理员"
        and (job.owner_id != current_user["user_id"] or job.owner_role != current_user["role"])
    ):
        raise HTTPException(status_code=404, detail="推理任务不存在")
    return job


@router.get("/options")
async def options(current_user: dict = Depends(get_current_user)):
    query = TrainingJob.filter(status="SUCCEEDED", cleanup_status="RETAINED")
    if current_user["role"] != "管理员":
        query = query.filter(owner_id=current_user["user_id"], owner_role=current_user["role"])
    jobs = await query.order_by("-finished_at").limit(200)
    algorithm_ids = {item.algorithm_id for item in jobs}
    dataset_ids = {item.dataset_id for item in jobs}
    algorithms = {
        item.id: item for item in await Algorithm.filter(id__in=algorithm_ids)
    } if algorithm_ids else {}
    datasets = dict(await Dataset.filter(id__in=dataset_ids).values_list("id", "name")) if dataset_ids else {}
    items = []
    for job in jobs:
        algorithm = algorithms.get(job.algorithm_id)
        adapter_key = (
            ((job.config_json or {}).get("adapter") or {}).get("key")
            or (algorithm.abbreviation if algorithm else "")
        )
        adapter = algorithm_adapter_registry.get(str(adapter_key))
        if adapter is None or not adapter.supports_inference or not job.remote_run_dir:
            continue
        try:
            server = gpu_server_registry.public_identity(job.server_id)
            server_service = gpu_server_registry.get(job.server_id)
        except GpuServerError:
            continue
        gpu_options = (
            inference_executor_service.config["gpu_allowlist"]
            if job.server_id == "primary"
            else list(range(int(server_service.config.get("expected_gpu_count") or 0)))
        )
        items.append({
            "id": job.id,
            "jobNo": job.job_no,
            "algorithmName": algorithm.name if algorithm else None,
            "algorithmAbbreviation": algorithm.abbreviation if algorithm else None,
            "datasetName": datasets.get(job.dataset_id),
            "serverId": server["server_id"],
            "serverName": server["server_name"],
            "serverHost": server["server_host"],
            "gpuOptions": gpu_options,
            "classes": ((job.config_json or {}).get("parameters") or {}).get("classes") or [],
            "finishedAt": job.finished_at,
        })
    return Result.success({
        "models": items,
        "maxConcurrentJobs": inference_executor_service.config["max_concurrent_jobs"],
    })


@router.post("/jobs")
async def create_job(
    request: InferenceJobCreate,
    current_user: dict = Depends(get_current_user),
):
    try:
        job = await inference_executor_service.submit_job(
            current_user,
            request.training_job_id,
            {"classes": request.classes},
            request.requested_gpu,
        )
        # 任务一旦提交成功，数据库中的 QUEUED 记录就是用户可追踪的事实。
        # 即时调度只是降低等待时间的优化；SSH、GPU 探测等瞬时故障不能把
        # 已经创建的任务伪装成“创建失败”，否则用户重试会产生重复任务。
        try:
            await inference_executor_service.dispatch_job(job.id)
        except InferenceExecutorError:
            logger.warning(
                "Inference job %s was persisted but immediate dispatch failed; "
                "the background scheduler will retry it",
                job.id,
                exc_info=True,
            )
        created = await InferenceJob.get(id=job.id)
        metadata = await _metadata([created])
        return Result.success(_data(created, metadata.get(created.id)))
    except InferenceExecutorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/jobs")
async def list_jobs(
    page_num: int = Query(1, alias="pageNum", ge=1),
    page_size: int = Query(10, alias="pageSize", ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    query = InferenceJob.all()
    if current_user["role"] != "管理员":
        query = query.filter(owner_id=current_user["user_id"], owner_role=current_user["role"])
    total = await query.count()
    jobs = await query.order_by("-submitted_at").offset((page_num - 1) * page_size).limit(page_size)
    metadata = await _metadata(jobs)
    return Result.success(PageInfo(
        total=total,
        list=[_data(job, metadata.get(job.id)) for job in jobs],
    ))


@router.get("/jobs/{job_id}")
async def get_job(job_id: int, current_user: dict = Depends(get_current_user)):
    job = await _accessible(job_id, current_user)
    try:
        job = await inference_executor_service.reconcile_job(job)
    except InferenceExecutorError:
        pass
    metadata = await _metadata([job])
    return Result.success(_data(job, metadata.get(job.id)))
