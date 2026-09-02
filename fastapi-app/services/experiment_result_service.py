"""训练与推理实验结果的统一只读索引和下载服务。

实验结果不再复制到第三套目录：训练以 ``TrainingArtifact`` 为索引，推理以
runner 的 ``result_json.visualizations`` 为索引，远程任务目录始终是文件事实源。
"""

from __future__ import annotations

import asyncio
import mimetypes
import posixpath
import tempfile
import zipfile
from pathlib import PurePosixPath
from typing import Any, BinaryIO

from models import Algorithm, Dataset, InferenceJob, TrainingArtifact, TrainingJob
from services.training_executor_service import TrainingExecutorError, training_executor_service
from services.training_reliability import safe_artifact_path


class ExperimentResultError(RuntimeError):
    pass


def _owned(query, current_user: dict[str, Any]):
    if current_user["role"] == "管理员":
        return query
    return query.filter(
        owner_id=current_user["user_id"],
        owner_role=current_user["role"],
    )


def _visualization_items(job: InferenceJob) -> list[dict[str, Any]]:
    result = job.result_json or {}
    raw_items = result.get("visualizationItems") or result.get("visualizations") or []
    items: list[dict[str, Any]] = []
    for raw in raw_items:
        if isinstance(raw, str):
            path, size = raw, 0
        elif isinstance(raw, dict):
            path = raw.get("path")
            size = raw.get("sizeBytes", raw.get("size_bytes", 0))
        else:
            continue
        pure = PurePosixPath(str(path or ""))
        if not path or pure.is_absolute() or ".." in pure.parts:
            continue
        items.append({"path": str(path), "sizeBytes": int(size or 0)})
    return items


class ExperimentResultService:
    async def _training_job(
        self,
        job_id: int,
        current_user: dict[str, Any],
    ) -> TrainingJob:
        job = await _owned(TrainingJob.filter(id=job_id), current_user).first()
        if job is None:
            raise ExperimentResultError("实验结果不存在")
        return job

    async def _inference_job(
        self,
        job_id: int,
        current_user: dict[str, Any],
    ) -> InferenceJob:
        job = await _owned(InferenceJob.filter(id=job_id), current_user).first()
        if job is None:
            raise ExperimentResultError("实验结果不存在")
        return job

    async def options(self, current_user: dict[str, Any]) -> dict[str, Any]:
        training = _owned(TrainingJob.filter(
            status="SUCCEEDED",
            artifacts__artifact_role="EVALUATION_VISUALIZATION",
            artifacts__downloadable=True,
        ).distinct(), current_user)
        inference = _owned(InferenceJob.filter(
            status="SUCCEEDED", result_json__isnull=False,
        ), current_user)
        training_rows = await training.values("algorithm_id", "dataset_id")
        inference_rows = await inference.values("training_job_id")
        inference_source_ids = {row["training_job_id"] for row in inference_rows}
        if inference_source_ids:
            training_rows.extend(
                await TrainingJob.filter(id__in=inference_source_ids).values(
                    "algorithm_id", "dataset_id"
                )
            )
        algorithm_ids = {row["algorithm_id"] for row in training_rows}
        dataset_ids = {row["dataset_id"] for row in training_rows}
        algorithms = await Algorithm.filter(id__in=algorithm_ids).order_by("name") if algorithm_ids else []
        datasets = await Dataset.filter(id__in=dataset_ids).order_by("name") if dataset_ids else []
        return {
            "algorithms": [
                {"id": item.id, "name": item.name, "abbreviation": item.abbreviation}
                for item in algorithms
            ],
            "datasets": [{"id": item.id, "name": item.name} for item in datasets],
            "sourceTypes": [
                {"value": "TRAINING", "label": "训练结果"},
                {"value": "INFERENCE", "label": "推理结果"},
            ],
        }

    async def list_runs(
        self,
        current_user: dict[str, Any],
        *,
        source_type: str,
        algorithm_id: int | None,
        dataset_id: int | None,
        page_num: int,
        page_size: int,
    ) -> tuple[int, list[dict[str, Any]]]:
        fetch_limit = page_num * page_size
        include_training = source_type in {"", "TRAINING"}
        include_inference = source_type in {"", "INFERENCE"}

        training_query = _owned(TrainingJob.filter(
            status="SUCCEEDED",
            artifacts__artifact_role="EVALUATION_VISUALIZATION",
            artifacts__downloadable=True,
        ).distinct(), current_user)
        if algorithm_id:
            training_query = training_query.filter(algorithm_id=algorithm_id)
        if dataset_id:
            training_query = training_query.filter(dataset_id=dataset_id)

        source_filter = TrainingJob.all()
        if algorithm_id:
            source_filter = source_filter.filter(algorithm_id=algorithm_id)
        if dataset_id:
            source_filter = source_filter.filter(dataset_id=dataset_id)
        source_ids = None
        if algorithm_id or dataset_id:
            source_ids = await source_filter.values_list("id", flat=True)
        inference_query = _owned(InferenceJob.filter(
            status="SUCCEEDED", result_json__isnull=False,
        ), current_user)
        if source_ids is not None:
            inference_query = inference_query.filter(training_job_id__in=source_ids)

        # MySQL JOIN 下 Tortoise 的 count() 不会可靠保留 DISTINCT，按主键去重
        # 才能保证分页总数等于用户实际看到的实验批次。
        training_total = (
            len(set(await training_query.values_list("id", flat=True)))
            if include_training else 0
        )
        inference_total = await inference_query.count() if include_inference else 0
        training_jobs = (
            await training_query.order_by("-finished_at", "-id").limit(fetch_limit)
            if include_training else []
        )
        inference_jobs = (
            await inference_query.order_by("-finished_at", "-id").limit(fetch_limit)
            if include_inference else []
        )

        source_jobs = {job.id: job for job in training_jobs}
        missing_source_ids = {
            job.training_job_id for job in inference_jobs
            if job.training_job_id not in source_jobs
        }
        if missing_source_ids:
            for job in await TrainingJob.filter(id__in=missing_source_ids):
                source_jobs[job.id] = job
        algorithm_ids = {job.algorithm_id for job in source_jobs.values()}
        dataset_ids = {job.dataset_id for job in source_jobs.values()}
        algorithms = {
            item.id: item for item in await Algorithm.filter(id__in=algorithm_ids)
        } if algorithm_ids else {}
        datasets = {
            item.id: item for item in await Dataset.filter(id__in=dataset_ids)
        } if dataset_ids else {}

        training_image_stats: dict[int, dict[str, int]] = {}
        if training_jobs:
            artifact_rows = await TrainingArtifact.filter(
                job_id__in=[job.id for job in training_jobs],
                artifact_role="EVALUATION_VISUALIZATION",
                downloadable=True,
            ).values("job_id", "size_bytes")
            for artifact in artifact_rows:
                stats = training_image_stats.setdefault(
                    artifact["job_id"], {"count": 0, "bytes": 0}
                )
                stats["count"] += 1
                stats["bytes"] += int(artifact["size_bytes"] or 0)

        rows: list[dict[str, Any]] = []
        for job in training_jobs:
            stats = training_image_stats.get(job.id, {"count": 0, "bytes": 0})
            rows.append(self._run_data(
                "TRAINING", job, job, algorithms, datasets,
                stats["count"], stats["bytes"],
            ))
        for job in inference_jobs:
            source = source_jobs.get(job.training_job_id)
            if source is None:
                continue
            images = _visualization_items(job)
            rows.append(self._run_data(
                "INFERENCE", job, source, algorithms, datasets,
                len(images), sum(item["sizeBytes"] for item in images),
            ))
        rows.sort(key=lambda item: item["finishedAt"] or item["submittedAt"], reverse=True)
        start = (page_num - 1) * page_size
        return training_total + inference_total, rows[start:start + page_size]

    @staticmethod
    def _run_data(
        source_type: str,
        job: TrainingJob | InferenceJob,
        source: TrainingJob,
        algorithms: dict[int, Algorithm],
        datasets: dict[int, Dataset],
        image_count: int,
        total_bytes: int,
    ) -> dict[str, Any]:
        algorithm = algorithms.get(source.algorithm_id)
        dataset = datasets.get(source.dataset_id)
        parameters = (source.config_json or {}).get("parameters") or {}
        return {
            "sourceType": source_type,
            "id": job.id,
            "jobNo": job.job_no,
            "trainingJobId": source.id,
            "algorithmId": source.algorithm_id,
            "algorithmName": algorithm.name if algorithm else None,
            "algorithmAbbreviation": algorithm.abbreviation if algorithm else None,
            "datasetId": source.dataset_id,
            "datasetName": dataset.name if dataset else None,
            "classes": (
                ((job.config_json or {}).get("parameters") or {}).get("classes")
                if source_type == "INFERENCE" else parameters.get("classes")
            ) or [],
            "imageCount": image_count,
            "totalBytes": total_bytes,
            "submittedAt": job.submitted_at,
            "finishedAt": job.finished_at,
        }

    async def list_images(
        self,
        source_type: str,
        job_id: int,
        current_user: dict[str, Any],
        page_num: int,
        page_size: int,
    ) -> tuple[int, list[dict[str, Any]]]:
        if source_type == "TRAINING":
            job = await self._training_job(job_id, current_user)
            try:
                await training_executor_service.ensure_artifact_catalog(job)
            except TrainingExecutorError as exc:
                raise ExperimentResultError("无法读取训练可视化产物") from exc
            query = TrainingArtifact.filter(
                job_id=job.id,
                artifact_role="EVALUATION_VISUALIZATION",
                downloadable=True,
            )
            total = await query.count()
            artifacts = await query.order_by("remote_path").offset(
                (page_num - 1) * page_size
            ).limit(page_size)
            return total, [
                {
                    "key": str(item.id),
                    "name": item.name,
                    "path": item.remote_path,
                    "sizeBytes": item.size_bytes,
                }
                for item in artifacts
            ]
        if source_type == "INFERENCE":
            job = await self._inference_job(job_id, current_user)
            items = _visualization_items(job)
            start = (page_num - 1) * page_size
            return len(items), [
                {
                    "key": str(index),
                    "name": posixpath.basename(item["path"]),
                    **item,
                }
                for index, item in enumerate(items[start:start + page_size], start=start)
            ]
        raise ExperimentResultError("实验结果来源类型无效")

    async def read_image(
        self,
        source_type: str,
        job_id: int,
        image_key: str,
        current_user: dict[str, Any],
    ) -> tuple[bytes, str, str]:
        if source_type == "TRAINING":
            job = await self._training_job(job_id, current_user)
            if not image_key.isdigit():
                raise ExperimentResultError("图片编号无效")
            artifact = await TrainingArtifact.get_or_none(
                id=int(image_key), job_id=job.id,
                artifact_role="EVALUATION_VISUALIZATION", downloadable=True,
            )
            if artifact is None or not safe_artifact_path(job.remote_run_dir or "", artifact.remote_path):
                raise ExperimentResultError("实验图片不存在")
            remote_path, name = artifact.remote_path, artifact.name
        elif source_type == "INFERENCE":
            job = await self._inference_job(job_id, current_user)
            items = _visualization_items(job)
            if not image_key.isdigit() or int(image_key) >= len(items):
                raise ExperimentResultError("实验图片不存在")
            relative = items[int(image_key)]["path"]
            if not job.remote_run_dir:
                raise ExperimentResultError("推理结果目录不可用")
            remote_path = posixpath.join(job.remote_run_dir, relative)
            name = posixpath.basename(relative)
        else:
            raise ExperimentResultError("实验结果来源类型无效")
        content = await self._read_remote_file(remote_path, 30 * 1024 * 1024)
        return content, mimetypes.guess_type(name)[0] or "application/octet-stream", name

    async def build_archive(
        self,
        source_type: str,
        job_id: int,
        current_user: dict[str, Any],
    ) -> tuple[BinaryIO, str]:
        entries: list[tuple[str, str]] = []
        if source_type == "TRAINING":
            job = await self._training_job(job_id, current_user)
            try:
                await training_executor_service.ensure_artifact_catalog(job)
            except TrainingExecutorError as exc:
                raise ExperimentResultError("无法读取训练可视化产物") from exc
            artifacts = await TrainingArtifact.filter(
                job_id=job.id,
                artifact_role="EVALUATION_VISUALIZATION",
                downloadable=True,
            ).order_by("remote_path")
            entries = [
                (item.remote_path, str(PurePosixPath(item.remote_path).relative_to(PurePosixPath(job.remote_run_dir))))
                for item in artifacts
                if safe_artifact_path(job.remote_run_dir or "", item.remote_path)
            ]
        elif source_type == "INFERENCE":
            job = await self._inference_job(job_id, current_user)
            if job.remote_run_dir:
                entries = [
                    (posixpath.join(job.remote_run_dir, item["path"]), item["path"])
                    for item in _visualization_items(job)
                ]
        else:
            raise ExperimentResultError("实验结果来源类型无效")
        if not entries:
            raise ExperimentResultError("该任务没有可下载的可视化结果")
        if len(entries) > 1000:
            raise ExperimentResultError("单次最多打包 1000 张实验图片")

        archive = tempfile.SpooledTemporaryFile(max_size=20 * 1024 * 1024)
        connection = await training_executor_service._connect()
        try:
            sftp = await connection.start_sftp_client()
            total_bytes = 0
            # 可视化产物均为已压缩图片：STORED 免去无谓的 deflate CPU；
            # 写入放线程池，避免大包压缩阻塞事件循环。
            with zipfile.ZipFile(
                archive, "w", compression=zipfile.ZIP_STORED
            ) as bundle:
                for remote_path, relative in entries:
                    attrs = await sftp.stat(remote_path)
                    size = int(attrs.size or 0)
                    total_bytes += size
                    if size > 30 * 1024 * 1024 or total_bytes > 500 * 1024 * 1024:
                        raise ExperimentResultError("实验结果压缩包超过安全大小限制")
                    async with sftp.open(remote_path, "rb") as stream:
                        content = await stream.read()
                    await asyncio.to_thread(bundle.writestr, relative, content)
        except ExperimentResultError:
            archive.close()
            raise
        except Exception as exc:
            archive.close()
            raise ExperimentResultError("部分远程实验图片已不存在") from exc
        finally:
            connection.close()
            await connection.wait_closed()
        archive.seek(0)
        return archive, f"{source_type.lower()}-{job.job_no}-visualizations.zip"

    async def _read_remote_file(self, remote_path: str, maximum: int) -> bytes:
        connection = await training_executor_service._connect()
        try:
            sftp = await connection.start_sftp_client()
            attrs = await sftp.stat(remote_path)
            if attrs.size is not None and attrs.size > maximum:
                raise ExperimentResultError("实验图片超过在线读取大小限制")
            async with sftp.open(remote_path, "rb") as stream:
                return await stream.read()
        except ExperimentResultError:
            raise
        except Exception as exc:
            raise ExperimentResultError("远程实验图片不存在") from exc
        finally:
            connection.close()
            await connection.wait_closed()


experiment_result_service = ExperimentResultService()


__all__ = [
    "ExperimentResultError",
    "ExperimentResultService",
    "experiment_result_service",
]
