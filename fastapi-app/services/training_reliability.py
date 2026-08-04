"""阶段 4 的失败分类、产物角色和路径边界规则。"""

from __future__ import annotations

import posixpath
import re
from pathlib import PurePosixPath


FAILURE_RULES = (
    (
        "CUDA_OOM",
        re.compile(
            r"CUDA out of memory|CUDNN_STATUS_ALLOC_FAILED|"
            r"CUDA error: out of memory",
            re.IGNORECASE,
        ),
        "GPU 显存不足，请减小批大小或输入尺寸后重试",
    ),
    (
        "DISK_FULL",
        re.compile(
            r"No space left on device|Disk quota exceeded",
            re.IGNORECASE,
        ),
        "训练磁盘空间不足，请清理历史任务产物后重试",
    ),
)


def classify_failure(
    exit_code: int | None,
    log_text: str,
    existing_code: str | None = None,
) -> tuple[str | None, str | None]:
    if existing_code == "TIMEOUT":
        return "TIMEOUT", "训练超过管理员设置的最长运行时间，已自动停止"
    if existing_code == "USER_STOPPED":
        return "USER_STOPPED", "用户主动停止训练"
    for code, pattern, reason in FAILURE_RULES:
        if pattern.search(log_text or ""):
            return code, reason
    if exit_code not in (None, 0):
        return "ABNORMAL_EXIT", f"训练进程异常退出（exit_code={exit_code}）"
    return None, None


def artifact_descriptor(relative_path: str) -> tuple[str, str, bool]:
    lower = relative_path.lower()
    name = posixpath.basename(lower)
    if name == "raw.log":
        return "LOG", "TRAIN_LOG", True
    if name == "config.json":
        return "CONFIG", "TRAIN_CONFIG", True
    if name == "command.json":
        return "CONFIG", "EXECUTION_COMMAND", True
    if name == "manifest.json":
        return "CONFIG", "RUN_MANIFEST", True
    if name == "results.csv":
        return "METRICS", "EVALUATION_RESULT", True
    if lower.endswith((".pth", ".pt", ".ckpt")):
        if "best" in name:
            return "CHECKPOINT", "BEST_CHECKPOINT", True
        if "last" in name or "latest" in name:
            return "CHECKPOINT", "LAST_CHECKPOINT", True
        return "CHECKPOINT", "AUXILIARY_CHECKPOINT", True
    if lower.endswith((".png", ".jpg", ".jpeg")):
        return "VISUALIZATION", "EVALUATION_VISUALIZATION", True
    if lower.endswith(".csv"):
        return "METRICS", "EVALUATION_RESULT", True
    if lower.endswith(".json"):
        return "CONFIG", "OTHER", True
    return "OTHER", "OTHER", False


def safe_artifact_path(run_directory: str, remote_path: str) -> bool:
    run_root = posixpath.normpath(run_directory)
    candidate = posixpath.normpath(remote_path)
    if not posixpath.isabs(run_root) or not posixpath.isabs(candidate):
        return False
    try:
        relative = PurePosixPath(candidate).relative_to(PurePosixPath(run_root))
    except ValueError:
        return False
    return bool(relative.parts) and ".." not in relative.parts


def hard_delete_blockers(
    *,
    status: str,
    archived: bool,
    cleanup_status: str,
    has_retry_children: bool,
    remote_paths_exist: bool,
    remote_process_exists: bool,
) -> list[str]:
    """返回彻底删除任务前尚未满足的安全条件。"""
    blockers = []
    if status not in {"SUCCEEDED", "FAILED", "STOPPED", "LOST"}:
        blockers.append("任务尚未结束")
    if not archived:
        blockers.append("任务尚未归档")
    if cleanup_status != "CLEANED":
        blockers.append("远程产物尚未清理")
    if has_retry_children:
        blockers.append("仍有重试任务引用当前任务")
    if remote_paths_exist:
        blockers.append("远程任务目录仍然存在")
    if remote_process_exists:
        blockers.append("远程训练进程仍然存在")
    return blockers
