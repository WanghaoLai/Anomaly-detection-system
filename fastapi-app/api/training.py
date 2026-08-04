"""阶段 2/3 训练任务管理、详情与 SSE 实时监控 API。"""

import asyncio
import json
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import StreamingResponse

from common.auth import get_current_user
from common.result import PageInfo, Result
from models import (
    Algorithm,
    Dataset,
    TrainingArtifact,
    TrainingAudit,
    TrainingEvent,
    TrainingJob,
    TrainingLog,
    TrainingMetric,
)
from services.training_executor_service import (
    TrainingExecutorError,
    training_executor_service,
)
from services.algorithm_adapters import algorithm_adapter_registry


router = APIRouter(
    prefix="/training",
    dependencies=[Depends(get_current_user)],
)


class TrainingJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    algorithm_id: int = Field(alias="algorithmId", gt=0)
    dataset_id: int = Field(alias="datasetId", gt=0)
    parameters: dict[str, Any] = Field(default_factory=dict)
    requested_gpu: int | None = Field(default=None, alias="requestedGpu", ge=0)


class TrainingJobHardDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_job_no: str = Field(alias="confirmJobNo", min_length=36, max_length=36)
    reason: str = Field(min_length=3, max_length=500)


def _job_data(
    job: TrainingJob,
    algorithm_name: str | None = None,
    dataset_name: str | None = None,
) -> dict[str, Any]:
    return {
        "id": job.id,
        "jobNo": job.job_no,
        "ownerId": job.owner_id,
        "ownerRole": job.owner_role,
        "algorithmId": job.algorithm_id,
        "algorithmName": algorithm_name,
        "datasetId": job.dataset_id,
        "datasetName": dataset_name,
        "status": job.status,
        "config": job.config_json,
        "assignedGpu": job.assigned_gpu,
        "remoteRunDir": job.remote_run_dir,
        "exitCode": job.exit_code,
        "failureCode": job.failure_code,
        "failureReason": job.failure_reason,
        "runtimeSnapshot": job.runtime_snapshot_json,
        "retryOfJobId": job.retry_of_job_id,
        "attempt": job.attempt,
        "progressPercent": job.progress_percent,
        "currentEpoch": job.current_epoch,
        "totalEpochs": job.total_epochs,
        "timeoutSeconds": job.timeout_seconds,
        "cleanupStatus": job.cleanup_status,
        "cleanedAt": job.cleaned_at,
        "reconcileFailures": job.reconcile_failures,
        "archivedAt": job.archived_at,
        "archivedBy": job.archived_by,
        "submittedAt": job.submitted_at,
        "startedAt": job.started_at,
        "finishedAt": job.finished_at,
        "lastReconciledAt": job.last_reconciled_at,
    }


async def _accessible_job(job_id: int, current_user: dict) -> TrainingJob:
    job = await TrainingJob.get_or_none(id=job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="训练任务不存在")
    if current_user["role"] != "管理员" and (
        job.owner_role != current_user["role"]
        or job.owner_id != current_user["user_id"]
    ):
        # 不泄露其他用户任务是否存在。
        raise HTTPException(status_code=404, detail="训练任务不存在")
    return job


@router.get("/options")
async def training_options():
    algorithm_allowlist = await training_executor_service.build_algorithm_allowlist()
    dataset_allowlist = await training_executor_service.build_dataset_allowlist()

    algorithms = await Algorithm.filter(
        abbreviation__in=list(algorithm_allowlist),
        deleted_at__isnull=True,
    ).prefetch_related("algorithm_infos").order_by("id")
    datasets = await Dataset.filter(
        name__in=list(dataset_allowlist),
        deleted_at__isnull=True,
    ).prefetch_related("dataset_infos").order_by("id")
    dataset_items = [
        {
            "id": dataset.id,
            "name": dataset.name,
            "description": dataset.description,
        }
        for dataset in datasets
        if dataset.dataset_infos
    ]
    algorithm_items = []
    for algorithm in algorithms:
        if not algorithm.algorithm_infos:
            continue
        info = algorithm.algorithm_infos[0]
        adapter = algorithm_adapter_registry.get(algorithm.abbreviation or "")
        if adapter is None:
            continue
        algorithm_items.append({
            "id": algorithm.id,
            "name": algorithm.name,
            "abbreviation": algorithm.abbreviation,
            "parameterSchema": info.parameter_schema_json,
            "datasetParameterSchemas": {
                str(dataset["id"]): adapter.parameter_schema_for_dataset(
                    info.parameter_schema_json,
                    dataset["name"],
                )
                for dataset in dataset_items
            },
            "resourceSpec": info.resource_spec_json,
            "datasetRequirement": info.dataset_requirement_json,
        })
    return Result.success({
        "algorithms": algorithm_items,
        "datasets": dataset_items,
        "gpuOptions": training_executor_service.config["gpu_allowlist"],
        "maxPendingJobs": training_executor_service.config[
            "max_pending_jobs_per_user"
        ],
        "maxConcurrentJobs": training_executor_service.config[
            "max_concurrent_jobs"
        ],
        "maxRuntimeSeconds": training_executor_service.config[
            "max_runtime_seconds"
        ],
        "artifactRetentionDays": training_executor_service.config[
            "artifact_retention_days"
        ],
    })


@router.post("/jobs")
async def create_job(
    request: TrainingJobCreate,
    current_user: dict = Depends(get_current_user),
):
    try:
        job = await training_executor_service.submit_job(
            owner=current_user,
            algorithm_id=request.algorithm_id,
            dataset_id=request.dataset_id,
            parameters=request.parameters,
            requested_gpu=request.requested_gpu,
        )
        # 立即触发一次调度；资源不足时保持 QUEUED。
        await training_executor_service.dispatch_queued_jobs()
        job = await TrainingJob.get(id=job.id)
        return Result.success(_job_data(job))
    except TrainingExecutorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/jobs")
async def list_jobs(
    status: str = "",
    archive_state: str = Query(default="active", alias="archiveState"),
    page_num: int = Query(default=1, alias="pageNum", ge=1),
    page_size: int = Query(default=10, alias="pageSize", ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    if archive_state not in {"active", "archived", "all"}:
        raise HTTPException(status_code=400, detail="归档筛选条件无效")
    query = TrainingJob.all()
    if current_user["role"] != "管理员":
        query = query.filter(
            owner_id=current_user["user_id"],
            owner_role=current_user["role"],
            archived_at__isnull=True,
        )
    elif archive_state == "active":
        query = query.filter(archived_at__isnull=True)
    elif archive_state == "archived":
        query = query.filter(archived_at__isnull=False)
    if status:
        query = query.filter(status=status)
    total = await query.count()
    jobs = await query.order_by("-submitted_at", "-id").offset(
        (page_num - 1) * page_size
    ).limit(page_size)
    algorithm_ids = {job.algorithm_id for job in jobs}
    dataset_ids = {job.dataset_id for job in jobs}
    algorithm_names = dict(
        await Algorithm.filter(id__in=algorithm_ids).values_list("id", "name")
    ) if algorithm_ids else {}
    dataset_names = dict(
        await Dataset.filter(id__in=dataset_ids).values_list("id", "name")
    ) if dataset_ids else {}
    return Result.success(PageInfo(
        total=total,
        list=[
            _job_data(
                job,
                algorithm_names.get(job.algorithm_id),
                dataset_names.get(job.dataset_id),
            )
            for job in jobs
        ],
    ))


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: int,
    refresh: bool = True,
    current_user: dict = Depends(get_current_user),
):
    job = await _accessible_job(job_id, current_user)
    if refresh and job.status in {"STARTING", "RUNNING", "STOPPING"}:
        try:
            job = await training_executor_service.reconcile_job(job)
        except TrainingExecutorError:
            pass
    elif refresh and job.remote_run_dir and not job.log_offset:
        # 阶段 1/2 的历史任务首次打开时回填阶段 3 日志与曲线。
        try:
            job = await training_executor_service.sync_job_output(job)
        except TrainingExecutorError:
            pass
    if job.status in {"SUCCEEDED", "FAILED", "STOPPED", "LOST"}:
        try:
            await training_executor_service.ensure_artifact_catalog(job)
        except TrainingExecutorError:
            pass
    algorithm = await Algorithm.get_or_none(id=job.algorithm_id)
    dataset = await Dataset.get_or_none(id=job.dataset_id)
    events = await TrainingEvent.filter(job_id=job.id).order_by("sequence")
    metrics = await TrainingMetric.filter(job_id=job.id).order_by("id")
    artifacts = await TrainingArtifact.filter(job_id=job.id).order_by(
        "artifact_type",
        "id",
    ).limit(500)
    recent_logs = await TrainingLog.filter(job_id=job.id).order_by("-id").limit(300)
    recent_logs.reverse()
    audits = await TrainingAudit.filter(job_id=job.id).order_by("-id").limit(200)
    audits.reverse()
    data = _job_data(
        job,
        algorithm.name if algorithm else None,
        dataset.name if dataset else None,
    )
    data.update({
        "events": [
            {
                "sequence": event.sequence,
                "type": event.event_type,
                "message": event.message,
                "payload": event.payload_json,
                "createdAt": event.created_at,
            }
            for event in events
        ],
        "metrics": [
            {
                "name": metric.metric_name,
                "value": metric.metric_value,
                "epoch": metric.epoch,
                "step": metric.step,
            }
            for metric in metrics
        ],
        "artifacts": [
            {
                "id": artifact.id,
                "type": artifact.artifact_type,
                "role": artifact.artifact_role,
                "name": artifact.name,
                "remotePath": artifact.remote_path,
                "sizeBytes": artifact.size_bytes,
                "downloadable": artifact.downloadable,
            }
            for artifact in artifacts
        ],
        "logs": [
            {
                "id": log.id,
                "sequence": log.sequence,
                "stream": log.stream,
                "content": log.content,
                "createdAt": log.created_at,
            }
            for log in recent_logs
        ],
        "audits": [
            {
                "id": audit.id,
                "actorId": audit.actor_id,
                "actorRole": audit.actor_role,
                "action": audit.action,
                "result": audit.result,
                "message": audit.message,
                "payload": audit.payload_json,
                "createdAt": audit.created_at,
            }
            for audit in audits
        ],
    })
    return Result.success(data)


async def _stream_state(job: TrainingJob) -> dict[str, Any]:
    metrics = await TrainingMetric.filter(job_id=job.id).order_by(
        "metric_name",
        "epoch",
        "id",
    )
    events = await TrainingEvent.filter(job_id=job.id).order_by("sequence")
    return {
        **_job_data(job),
        "metrics": [
            {
                "name": metric.metric_name,
                "value": metric.metric_value,
                "epoch": metric.epoch,
                "step": metric.step,
            }
            for metric in metrics
        ],
        "events": [
            {
                "sequence": event.sequence,
                "type": event.event_type,
                "message": event.message,
                "payload": event.payload_json,
                "createdAt": event.created_at,
            }
            for event in events
        ],
    }


def _sse_message(event: str, data: Any, event_id: int | None = None) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    payload = json.dumps(data, ensure_ascii=False, default=str)
    lines.extend(f"data: {line}" for line in payload.splitlines() or ["{}"])
    return "\n".join(lines) + "\n\n"


@router.get("/jobs/{job_id}/stream")
async def stream_job(
    job_id: int,
    request: Request,
    after_log_id: int = Query(default=0, alias="afterLogId", ge=0),
    current_user: dict = Depends(get_current_user),
):
    job = await _accessible_job(job_id, current_user)
    if job.remote_run_dir:
        try:
            job = await training_executor_service.sync_job_output(job)
        except TrainingExecutorError:
            pass

    async def event_stream():
        cursor = after_log_id
        if cursor == 0:
            recent = await TrainingLog.filter(job_id=job.id).order_by("-id").limit(300)
            recent.reverse()
            if recent:
                cursor = recent[-1].id
            snapshot = await _stream_state(await TrainingJob.get(id=job.id))
            snapshot["logs"] = [
                {
                    "id": item.id,
                    "sequence": item.sequence,
                    "stream": item.stream,
                    "content": item.content,
                    "createdAt": item.created_at,
                }
                for item in recent
            ]
            snapshot["lastLogId"] = cursor
            yield _sse_message("snapshot", snapshot)

        last_state = ""
        keepalive_tick = 0
        while not await request.is_disconnected():
            current = await TrainingJob.get(id=job.id)
            logs = await TrainingLog.filter(
                job_id=job.id,
                id__gt=cursor,
            ).order_by("id").limit(200)
            for item in logs:
                cursor = item.id
                yield _sse_message(
                    "log",
                    {
                        "id": item.id,
                        "sequence": item.sequence,
                        "stream": item.stream,
                        "content": item.content,
                        "createdAt": item.created_at,
                    },
                    item.id,
                )

            state = await _stream_state(current)
            state_key = json.dumps(state, ensure_ascii=False, default=str, sort_keys=True)
            if state_key != last_state:
                yield _sse_message("state", state)
                last_state = state_key

            if current.status in {"SUCCEEDED", "FAILED", "STOPPED", "LOST"}:
                yield _sse_message("done", {"status": current.status})
                return
            keepalive_tick += 1
            if keepalive_tick >= 15:
                yield ": keepalive\n\n"
                keepalive_tick = 0
            await asyncio.sleep(1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: int,
    current_user: dict = Depends(get_current_user),
):
    job = await _accessible_job(job_id, current_user)
    try:
        job = await training_executor_service.cancel_job(job.id)
        await training_executor_service.audit(
            job.id,
            "JOB_CANCEL",
            current_user,
            "用户取消排队任务",
        )
        return Result.success(_job_data(job))
    except TrainingExecutorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/stop")
async def stop_job(
    job_id: int,
    current_user: dict = Depends(get_current_user),
):
    job = await _accessible_job(job_id, current_user)
    if job.status not in {"STARTING", "RUNNING", "STOPPING"}:
        raise HTTPException(status_code=400, detail="当前任务状态不能停止")
    try:
        job = await training_executor_service.stop_job(job.id)
        await training_executor_service.audit(
            job.id,
            "JOB_STOP",
            current_user,
            "用户请求停止训练",
        )
        return Result.success(_job_data(job))
    except TrainingExecutorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/retry")
async def retry_job(
    job_id: int,
    current_user: dict = Depends(get_current_user),
):
    source = await _accessible_job(job_id, current_user)
    if source.archived_at is not None:
        raise HTTPException(status_code=400, detail="请先恢复归档任务再重试")
    try:
        job = await training_executor_service.retry_job(
            source.id,
            current_user,
        )
        await training_executor_service.audit(
            source.id,
            "JOB_RETRY",
            current_user,
            "从当前任务创建重试",
            {"new_job_id": job.id},
        )
        await training_executor_service.dispatch_queued_jobs()
        return Result.success(_job_data(await TrainingJob.get(id=job.id)))
    except TrainingExecutorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/archive")
async def archive_job(
    job_id: int,
    current_user: dict = Depends(get_current_user),
):
    if current_user["role"] != "管理员":
        raise HTTPException(status_code=403, detail="仅管理员可以归档训练任务")
    job = await _accessible_job(job_id, current_user)
    try:
        archived = await training_executor_service.archive_job(job, current_user)
        return Result.success(_job_data(archived))
    except TrainingExecutorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/restore")
async def restore_job(
    job_id: int,
    current_user: dict = Depends(get_current_user),
):
    if current_user["role"] != "管理员":
        raise HTTPException(status_code=403, detail="仅管理员可以恢复归档任务")
    job = await _accessible_job(job_id, current_user)
    try:
        restored = await training_executor_service.restore_archived_job(
            job,
            current_user,
        )
        return Result.success(_job_data(restored))
    except TrainingExecutorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/hard-delete")
async def hard_delete_job(
    job_id: int,
    request: TrainingJobHardDelete,
    current_user: dict = Depends(get_current_user),
):
    if current_user["role"] != "管理员":
        raise HTTPException(status_code=403, detail="仅管理员可以彻底删除训练任务")
    job = await _accessible_job(job_id, current_user)
    try:
        await training_executor_service.hard_delete_job(
            job,
            current_user,
            request.confirm_job_no,
            request.reason,
        )
        return Result.success({"id": job.id, "jobNo": job.job_no})
    except TrainingExecutorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/jobs/{job_id}/artifacts/{artifact_id}/download")
async def download_artifact(
    job_id: int,
    artifact_id: int,
    current_user: dict = Depends(get_current_user),
):
    job = await _accessible_job(job_id, current_user)
    artifact = await TrainingArtifact.get_or_none(
        id=artifact_id,
        job_id=job.id,
    )
    if artifact is None:
        raise HTTPException(status_code=404, detail="训练产物不存在")
    try:
        size = await training_executor_service.artifact_size(job, artifact)
        await training_executor_service.audit(
            job.id,
            "ARTIFACT_DOWNLOAD",
            current_user,
            f"下载训练产物：{artifact.name}",
            {
                "artifact_id": artifact.id,
                "artifact_role": artifact.artifact_role,
                "size_bytes": size,
            },
        )
        encoded_name = quote(artifact.name, safe="")
        return StreamingResponse(
            training_executor_service.stream_artifact(artifact),
            media_type="application/octet-stream",
            headers={
                "Content-Length": str(size),
                "Content-Disposition": (
                    f"attachment; filename*=UTF-8''{encoded_name}"
                ),
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
    except TrainingExecutorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/jobs/{job_id}/cleanup-preview")
async def cleanup_preview(
    job_id: int,
    current_user: dict = Depends(get_current_user),
):
    if current_user["role"] != "管理员":
        raise HTTPException(status_code=403, detail="仅管理员可以清理训练产物")
    job = await _accessible_job(job_id, current_user)
    artifacts = await TrainingArtifact.filter(job_id=job.id)
    return Result.success({
        "jobId": job.id,
        "cleanupStatus": job.cleanup_status,
        "artifactCount": len(artifacts),
        "totalBytes": sum(item.size_bytes for item in artifacts),
        "remoteRunDir": job.remote_run_dir,
    })


@router.post("/jobs/{job_id}/cleanup")
async def cleanup_job(
    job_id: int,
    current_user: dict = Depends(get_current_user),
):
    if current_user["role"] != "管理员":
        raise HTTPException(status_code=403, detail="仅管理员可以清理训练产物")
    job = await _accessible_job(job_id, current_user)
    try:
        cleaned = await training_executor_service.cleanup_job_artifacts(
            job,
            current_user,
        )
        return Result.success(_job_data(cleaned))
    except TrainingExecutorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
