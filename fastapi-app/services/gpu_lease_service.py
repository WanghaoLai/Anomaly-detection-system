"""训练与推理共用的数据库 GPU 租约协调器。"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from models import GpuLease, InferenceJob, TrainingJob
from tortoise.exceptions import IntegrityError
from tortoise.transactions import in_transaction

TRAINING_LEASE_STATUSES = frozenset({"STARTING", "RUNNING", "STOPPING"})
INFERENCE_LEASE_STATUSES = frozenset({"STARTING", "RUNNING"})


class GpuLeaseError(RuntimeError):
    pass


class GpuLeaseCoordinator:
    """以 ``gpu_leases.gpu_index`` 唯一键实现跨进程、跨任务表互斥。"""

    @staticmethod
    async def _remove_stale_leases(connection) -> None:
        leases = await GpuLease.all().using_db(connection).select_for_update()
        for lease in leases:
            if lease.workload_type == "TRAINING":
                job = await TrainingJob.filter(id=lease.workload_id).using_db(
                    connection
                ).first()
                active = (
                    job is not None
                    and job.status in TRAINING_LEASE_STATUSES
                    and job.assigned_gpu == lease.gpu_index
                )
            elif lease.workload_type == "INFERENCE":
                job = await InferenceJob.filter(id=lease.workload_id).using_db(
                    connection
                ).first()
                active = (
                    job is not None
                    and job.status in INFERENCE_LEASE_STATUSES
                    and job.assigned_gpu == lease.gpu_index
                )
            else:
                active = False
            if not active:
                await GpuLease.filter(gpu_index=lease.gpu_index).using_db(
                    connection
                ).delete()

    @staticmethod
    async def _backfill_active_leases(connection) -> None:
        """为迁移前已经运行的任务补建租约，避免升级后错误复用 GPU。"""

        training_jobs = await TrainingJob.filter(
            status__in=TRAINING_LEASE_STATUSES,
            assigned_gpu__isnull=False,
        ).using_db(connection)
        inference_jobs = await InferenceJob.filter(
            status__in=INFERENCE_LEASE_STATUSES,
            assigned_gpu__isnull=False,
        ).using_db(connection)
        workloads = [
            ("TRAINING", job) for job in training_jobs
        ] + [
            ("INFERENCE", job) for job in inference_jobs
        ]
        for workload_type, job in workloads:
            existing_workload = await GpuLease.filter(
                workload_type=workload_type,
                workload_id=job.id,
            ).using_db(connection).first()
            if existing_workload is not None:
                continue
            existing_gpu = await GpuLease.filter(
                gpu_index=job.assigned_gpu,
            ).using_db(connection).first()
            if existing_gpu is not None:
                # 历史数据已经存在同卡双任务时保留首个租约；该 GPU 仍会被
                # 整体阻塞，不会再分配给新任务。既有远程进程留给监控收敛。
                continue
            await GpuLease.create(
                gpu_index=job.assigned_gpu,
                workload_type=workload_type,
                workload_id=job.id,
                using_db=connection,
            )

    async def acquire(
        self,
        *,
        workload_type: str,
        workload_id: int,
        candidates: Iterable[int],
    ) -> int | None:
        normalized_type = str(workload_type).strip().upper()
        if normalized_type not in {"TRAINING", "INFERENCE"}:
            raise GpuLeaseError("GPU 租约任务类型无效")
        candidate_list = list(dict.fromkeys(int(item) for item in candidates))
        if not candidate_list:
            return None

        # 每个候选使用独立事务。唯一键冲突只回滚当前候选，不会污染后续尝试。
        for gpu_index in candidate_list:
            try:
                async with in_transaction() as connection:
                    await self._remove_stale_leases(connection)
                    existing = await GpuLease.filter(
                        workload_type=normalized_type,
                        workload_id=workload_id,
                    ).using_db(connection).first()
                    if existing is not None:
                        # 同一任务已被别的 worker 认领。返回已有 GPU 会让两个
                        # worker 都继续启动远程进程，因此必须把本次认领判为失败。
                        return None
                    await GpuLease.create(
                        gpu_index=gpu_index,
                        workload_type=normalized_type,
                        workload_id=workload_id,
                        using_db=connection,
                    )
                    model = (
                        TrainingJob
                        if normalized_type == "TRAINING"
                        else InferenceJob
                    )
                    updated = await model.filter(
                        id=workload_id,
                        status="QUEUED",
                    ).using_db(connection).update(
                        status="STARTING",
                        assigned_gpu=gpu_index,
                        # 与状态和租约在同一事务写入，消除监控在两次更新之间
                        # 把排队已久的任务误判为启动超时的窗口。
                        started_at=datetime.now(UTC),
                    )
                    if updated != 1:
                        raise GpuLeaseError("任务状态已变化，停止 GPU 调度")
                return gpu_index
            except IntegrityError:
                # 另一进程刚刚认领此 GPU 或同一任务已由另一调度器认领。
                continue
            except GpuLeaseError:
                # 任务在事务提交前已不再处于 QUEUED，租约随事务一并回滚。
                return None
        return None

    @staticmethod
    async def release(workload_type: str, workload_id: int) -> None:
        await GpuLease.filter(
            workload_type=str(workload_type).strip().upper(),
            workload_id=workload_id,
        ).delete()

    async def reconcile(self) -> None:
        async with in_transaction() as connection:
            await self._remove_stale_leases(connection)
            await self._backfill_active_leases(connection)


gpu_lease_coordinator = GpuLeaseCoordinator()


__all__ = [
    "GpuLeaseCoordinator",
    "GpuLeaseError",
    "gpu_lease_coordinator",
]
