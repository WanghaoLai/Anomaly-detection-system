"""阶段 1 内部验收 API；阶段 2 再提供普通用户训练管理页面。"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from common.auth import get_current_admin
from common.result import Result
from models import TrainingArtifact, TrainingEvent, TrainingJob, TrainingMetric
from services.training_executor_service import (
    TrainingExecutorError,
    training_executor_service,
)


router = APIRouter(
    prefix="/training-internal",
    dependencies=[Depends(get_current_admin)],
)


class Phase1JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    algorithm_id: int = Field(alias="algorithmId", gt=0)
    dataset_id: int = Field(alias="datasetId", gt=0)
    parameters: dict[str, Any] = Field(default_factory=dict)
    requested_gpu: int | None = Field(default=None, alias="requestedGpu", ge=0)


def _job_data(job: TrainingJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "jobNo": job.job_no,
        "ownerId": job.owner_id,
        "ownerRole": job.owner_role,
        "algorithmId": job.algorithm_id,
        "datasetId": job.dataset_id,
        "status": job.status,
        "assignedGpu": job.assigned_gpu,
        "launcherPid": job.launcher_pid,
        "workerPid": job.worker_pid,
        "processPid": job.process_pid,
        "processPgid": job.process_pgid,
        "remoteControlDir": job.remote_control_dir,
        "remoteRunDir": job.remote_run_dir,
        "exitCode": job.exit_code,
        "failureReason": job.failure_reason,
        "runtimeSnapshot": job.runtime_snapshot_json,
        "submittedAt": job.submitted_at,
        "startedAt": job.started_at,
        "finishedAt": job.finished_at,
        "lastReconciledAt": job.last_reconciled_at,
    }


@router.get("/health")
async def executor_health():
    return Result.success({
        "enabled": training_executor_service.enabled,
        "account": training_executor_service.config["ssh_user"],
        "gpuAllowlist": training_executor_service.config["gpu_allowlist"],
    })


@router.post("/jobs")
async def create_job(
    request: Phase1JobCreate,
    current_admin: dict = Depends(get_current_admin),
):
    try:
        job = await training_executor_service.create_job(
            owner=current_admin,
            algorithm_id=request.algorithm_id,
            dataset_id=request.dataset_id,
            parameters=request.parameters,
            requested_gpu=request.requested_gpu,
        )
        return Result.success(_job_data(job))
    except TrainingExecutorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/jobs/{job_id}")
async def get_job(job_id: int, refresh: bool = True):
    job = await TrainingJob.get_or_none(id=job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="训练任务不存在")
    if refresh:
        try:
            job = await training_executor_service.reconcile_job(job)
        except TrainingExecutorError:
            # 查询仍返回数据库快照，连接异常由 failureReason/后端日志诊断。
            pass
    events = await TrainingEvent.filter(job_id=job.id).order_by("sequence")
    metrics = await TrainingMetric.filter(job_id=job.id).order_by("id")
    artifacts = await TrainingArtifact.filter(job_id=job.id).order_by("id")
    data = _job_data(job)
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
                "type": artifact.artifact_type,
                "name": artifact.name,
                "remotePath": artifact.remote_path,
                "sizeBytes": artifact.size_bytes,
            }
            for artifact in artifacts
        ],
    })
    return Result.success(data)


@router.post("/jobs/{job_id}/stop")
async def stop_job(job_id: int):
    try:
        job = await training_executor_service.stop_job(job_id)
        return Result.success(_job_data(job))
    except TrainingExecutorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
