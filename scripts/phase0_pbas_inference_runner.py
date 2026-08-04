#!/usr/bin/env python3
"""PBAS checkpoint inference runner.

The official PBAS implementation uses the same ``main.py`` command for
training and testing, switching ``--test ckpt`` to ``--test test``.  This
runner therefore replays the immutable training argv after validating and
minimally transforming it.  It clones artifacts first, so inference can never
overwrite the source checkpoint.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
TOP_LEVEL_KEYS = {
    "version", "algorithm", "runtime", "dataset", "resources", "training",
    "source", "output",
}


class ConfigError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def absolute(value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ConfigError(f"{field} 必须是非空绝对路径")
    return Path(value)


def load_and_validate(path: Path, check_files: bool = True) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"无法读取推理配置: {exc}") from exc
    if not isinstance(config, dict) or set(config) != TOP_LEVEL_KEYS:
        raise ConfigError("推理配置顶层字段不完整或包含未知字段")
    if config.get("version") != 1 or config.get("algorithm") != "PBAS":
        raise ConfigError("当前推理 runner 仅支持 PBAS version=1")
    objects = {}
    for name in TOP_LEVEL_KEYS - {"version", "algorithm"}:
        value = config.get(name)
        if not isinstance(value, dict):
            raise ConfigError(f"{name} 必须是对象")
        objects[name] = value
    runtime, dataset = objects["runtime"], objects["dataset"]
    python_path = absolute(runtime.get("python_executable"), "runtime.python_executable")
    source_dir = absolute(runtime.get("source_directory"), "runtime.source_directory")
    if runtime.get("entrypoint") != "main.py":
        raise ConfigError("PBAS 推理入口必须复用已适配的 main.py")
    dataset_root = absolute(dataset.get("root_directory"), "dataset.root_directory")
    source_run = absolute(objects["source"].get("run_directory"), "source.run_directory")
    output_root = absolute(objects["output"].get("root_directory"), "output.root_directory")
    classes = dataset.get("classes")
    if (
        not isinstance(classes, list) or not classes
        or len(set(classes)) != len(classes)
        or any(not isinstance(item, str) or not item for item in classes)
    ):
        raise ConfigError("dataset.classes 必须是非空且不重复的字符串数组")
    gpu = objects["resources"].get("gpu_index")
    if isinstance(gpu, bool) or not isinstance(gpu, int) or not 0 <= gpu <= 63:
        raise ConfigError("resources.gpu_index 无效")
    command_path = source_run / "command.json"
    artifacts_dir = source_run / "artifacts"
    if check_files:
        for candidate, label in (
            (python_path, "Conda Python"),
            (source_dir / "main.py", "PBAS main.py"),
            (dataset_root, "数据集目录"),
            (command_path, "训练命令快照"),
            (artifacts_dir, "训练产物目录"),
        ):
            if not candidate.exists():
                raise ConfigError(f"{label}不存在: {candidate}")
        if not any(artifacts_dir.rglob("ckpt_best*.pth")):
            raise ConfigError("训练任务不存在最佳 checkpoint")
    return {
        "python": python_path,
        "source_dir": source_dir,
        "dataset_root": dataset_root,
        "dataset_loader": str(dataset.get("name")),
        "classes": classes,
        "gpu": gpu,
        "source_run": source_run,
        "output_root": output_root,
        "command_path": command_path,
        "artifacts_dir": artifacts_dir,
    }


def build_command(values: dict[str, Any], target_artifacts: Path) -> list[str]:
    try:
        snapshot = json.loads(values["command_path"].read_text(encoding="utf-8"))
        original = snapshot["argv"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ConfigError(f"训练命令快照无效: {exc}") from exc
    if not isinstance(original, list) or any(not isinstance(x, str) for x in original):
        raise ConfigError("训练 argv 必须是字符串数组")
    expected_prefix = [str(values["python"]), str(values["source_dir"] / "main.py")]
    if original[:2] != expected_prefix or "--test" not in original or "--results_path" not in original:
        raise ConfigError("训练 argv 与当前白名单运行时不一致")
    command = list(original)
    command[command.index("--test") + 1] = "test"
    command[command.index("--results_path") + 1] = str(target_artifacts)

    # 只允许收窄至训练任务已声明的类别；数据加载器及根目录保持原样。
    filtered: list[str] = []
    index = 0
    while index < len(command):
        if command[index] == "-d":
            index += 2
            continue
        filtered.append(command[index])
        index += 1
    dataset_root = str(values["dataset_root"])
    if dataset_root not in filtered:
        raise ConfigError("训练 argv 的数据集根目录与当前配置不一致")
    root_index = filtered.index(dataset_root)
    # Click 的 chained group 要求 dataset 子命令选项位于两个位置参数
    # (loader, data_path) 之前，训练 runner 的顺序也是 -d CLASS loader root。
    insert_index = root_index - 1
    if insert_index < 0:
        raise ConfigError("训练 argv 缺少数据集 loader")
    for class_name in values["classes"]:
        filtered[insert_index:insert_index] = ["-d", class_name]
        insert_index += 2
    return filtered


def collect_result(run_dir: Path) -> dict[str, Any]:
    csv_files = sorted(run_dir.rglob("results.csv"), key=lambda p: p.stat().st_mtime)
    rows: list[dict[str, Any]] = []
    if csv_files:
        with csv_files[-1].open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                converted: dict[str, Any] = {}
                for key, value in row.items():
                    try:
                        converted[key] = float(value) if value not in (None, "") else value
                    except ValueError:
                        converted[key] = value
                rows.append(converted)
    image_paths = [
        path
        for path in sorted(run_dir.rglob("*"))
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg"}
    ][:500]
    images = [str(path.relative_to(run_dir)) for path in image_paths]
    return {
        "metrics": rows,
        # 保留字符串列表兼容已上线页面；结构化清单供统一结果中心高效读取大小。
        "visualizations": images,
        "visualizationItems": [
            {
                "path": str(path.relative_to(run_dir)),
                "sizeBytes": path.stat().st_size,
            }
            for path in image_paths
        ],
    }


def copy_model_artifacts(source: Path, target: Path) -> int:
    """只复制推理必需的 checkpoint，避免重复保存训练指标和可视化图片。"""
    copied = 0
    target.mkdir(parents=True)
    for path in sorted(source.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".pth", ".pt", ".ckpt"}:
            continue
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        copied += 1
    if copied == 0:
        raise ConfigError("训练产物目录中没有可供推理的 checkpoint")
    return copied


def run(config_path: Path, run_id: str, check_only: bool) -> int:
    values = load_and_validate(config_path)
    if check_only:
        print("PBAS checkpoint、训练命令和数据集检查通过")
        return 0
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ConfigError("run-id 格式无效")
    run_dir = values["output_root"] / run_id
    try:
        run_dir.mkdir(parents=True)
    except FileExistsError as exc:
        raise ConfigError(f"推理目录已存在，拒绝覆盖: {run_dir}") from exc
    target_artifacts = run_dir / "artifacts"
    copy_model_artifacts(values["artifacts_dir"], target_artifacts)
    work_dir = run_dir / "work"
    work_dir.mkdir()
    shutil.copyfile(config_path, run_dir / "config.json")
    command = build_command(values, target_artifacts)
    write_json(run_dir / "command.json", {"argv": command})
    manifest = {
        "protocol_version": "pbas-inference-v1",
        "run_id": run_id,
        "status": "RUNNING",
        "started_at": utc_now(),
        "finished_at": None,
        "exit_code": None,
    }
    write_json(run_dir / "manifest.json", manifest)
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = str(values["gpu"])
    env["PYTHONUNBUFFERED"] = "1"
    started = time.monotonic()
    with (run_dir / "raw.log").open("w", encoding="utf-8") as log:
        completed = subprocess.run(
            command,
            # 与训练 runner 一致地隔离相对路径输出。PBAS 的可视化默认写到
            # ./results，绝不能让推理污染只读算法源码目录。
            cwd=work_dir,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    result = collect_result(run_dir)
    write_json(run_dir / "result.json", result)
    manifest.update({
        "status": "SUCCEEDED" if completed.returncode == 0 else "FAILED",
        "finished_at": utc_now(),
        "duration_seconds": round(time.monotonic() - started, 3),
        "exit_code": completed.returncode,
        "result": result,
    })
    write_json(run_dir / "manifest.json", manifest)
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="PBAS checkpoint 推理 runner")
    parser.add_argument("--config", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    try:
        return run(args.config.resolve(), args.run_id, args.check)
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"执行错误: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
