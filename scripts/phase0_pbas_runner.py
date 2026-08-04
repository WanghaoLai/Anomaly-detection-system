#!/usr/bin/env python3
"""PBAS industrial anomaly-dataset training runner.

It turns a small JSON configuration into a fixed argv list and never invokes a
shell. Supported dataset loaders are explicitly allowlisted.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DATASET_PROFILES = {
    "MVTec AD": {
        "loader": "mvtec",
        "classes": {
            "bottle", "cable", "capsule", "carpet", "grid", "hazelnut",
            "leather", "metal_nut", "pill", "screw", "tile", "toothbrush",
            "transistor", "wood", "zipper",
        },
        "layout": "directory",
    },
    "VisA": {
        "loader": "visa",
        "classes": {
            "candle", "capsules", "cashew", "chewinggum", "fryum",
            "macaroni1", "macaroni2", "pcb1", "pcb2", "pcb3", "pcb4",
            "pipe_fryum",
        },
        "layout": "visa_csv",
    },
    "MPDD": {
        "loader": "mpdd",
        "classes": {
            "bracket_black", "bracket_brown", "bracket_white", "connector",
            "metal_plate", "tubes",
        },
        "layout": "directory",
    },
    "BTAD": {
        "loader": "btad",
        "classes": {"01", "02", "03"},
        "layout": "directory",
    },
}
EXPECTED_TOP_LEVEL_KEYS = {
    "version",
    "algorithm",
    "runtime",
    "dataset",
    "resources",
    "training",
    "output",
}
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


class ConfigError(ValueError):
    """Raised when a phase-0 configuration is unsafe or incomplete."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def load_config(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"配置文件不存在: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"配置文件不是有效 JSON: {exc}") from exc
    if not isinstance(config, dict):
        raise ConfigError("配置文件顶层必须是 JSON 对象")
    unknown = set(config) - EXPECTED_TOP_LEVEL_KEYS
    missing = EXPECTED_TOP_LEVEL_KEYS - set(config)
    if unknown:
        raise ConfigError(f"存在未支持的顶层字段: {', '.join(sorted(unknown))}")
    if missing:
        raise ConfigError(f"缺少顶层字段: {', '.join(sorted(missing))}")
    return config


def require_object(config: dict[str, Any], name: str) -> dict[str, Any]:
    value = config.get(name)
    if not isinstance(value, dict):
        raise ConfigError(f"{name} 必须是 JSON 对象")
    return value


def require_absolute_path(value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field} 必须是非空绝对路径")
    path = Path(value)
    if not path.is_absolute():
        raise ConfigError(f"{field} 必须是绝对路径")
    return path


def bounded_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{field} 必须是整数")
    if not minimum <= value <= maximum:
        raise ConfigError(f"{field} 必须在 {minimum} 到 {maximum} 之间")
    return value


def validate_config(config: dict[str, Any], check_files: bool = True) -> dict[str, Any]:
    if config["version"] != 1:
        raise ConfigError("当前仅支持 version=1")
    if config["algorithm"] != "PBAS":
        raise ConfigError("第 0 阶段仅允许 PBAS")

    runtime = require_object(config, "runtime")
    dataset = require_object(config, "dataset")
    resources = require_object(config, "resources")
    training = require_object(config, "training")
    output = require_object(config, "output")

    python_executable = require_absolute_path(
        runtime.get("python_executable"), "runtime.python_executable"
    )
    source_directory = require_absolute_path(
        runtime.get("source_directory"), "runtime.source_directory"
    )
    if runtime.get("entrypoint") != "main.py":
        raise ConfigError("第 0 阶段 PBAS 入口必须固定为 main.py")
    entrypoint = source_directory / "main.py"

    dataset_name = dataset.get("name")
    profile = DATASET_PROFILES.get(dataset_name)
    if profile is None:
        raise ConfigError(
            f"PBAS 不支持数据集 {dataset_name}；"
            f"当前支持：{', '.join(DATASET_PROFILES)}"
        )
    dataset_root = require_absolute_path(dataset.get("root_directory"), "dataset.root_directory")
    classes = dataset.get("classes")
    if (
        not isinstance(classes, list)
        or not classes
        or any(
            not isinstance(item, str) or item not in profile["classes"]
            for item in classes
        )
        or len(set(classes)) != len(classes)
    ):
        raise ConfigError(
            f"dataset.classes 包含空值、重复值或非 {dataset_name} 类别"
        )

    gpu_index = bounded_int(resources.get("gpu_index"), "resources.gpu_index", 0, 63)
    epochs = bounded_int(training.get("epochs"), "training.epochs", 1, 10)
    batch_size = bounded_int(training.get("batch_size"), "training.batch_size", 1, 64)
    num_workers = bounded_int(training.get("num_workers"), "training.num_workers", 0, 32)
    resize = bounded_int(training.get("resize"), "training.resize", 64, 1024)
    image_size = bounded_int(training.get("image_size"), "training.image_size", 64, 1024)
    seed = bounded_int(training.get("seed"), "training.seed", 0, 2**31 - 1)
    eval_every = bounded_int(training.get("eval_every"), "training.eval_every", 1, epochs)
    learning_rate = training.get("learning_rate")
    if (
        isinstance(learning_rate, bool)
        or not isinstance(learning_rate, (int, float))
        or not 0 < float(learning_rate) <= 1
    ):
        raise ConfigError("training.learning_rate 必须大于 0 且不超过 1")

    output_root = require_absolute_path(output.get("root_directory"), "output.root_directory")

    if check_files:
        if not python_executable.is_file() or not os.access(python_executable, os.X_OK):
            raise ConfigError(f"Conda Python 不存在或不可执行: {python_executable}")
        if not source_directory.is_dir():
            raise ConfigError(f"PBAS 源码目录不存在: {source_directory}")
        if not entrypoint.is_file():
            raise ConfigError(f"PBAS 训练入口不存在: {entrypoint}")
        if not dataset_root.is_dir():
            raise ConfigError(f"{dataset_name} 根目录不存在: {dataset_root}")
        if profile["layout"] == "visa_csv":
            split_csv = dataset_root / "split_csv" / "1cls.csv"
            if not split_csv.is_file():
                raise ConfigError(f"VisA 划分文件不存在: {split_csv}")
        for class_name in classes:
            class_dir = dataset_root / class_name
            if profile["layout"] == "directory":
                if (
                    not (class_dir / "train").is_dir()
                    or not (class_dir / "test").is_dir()
                ):
                    raise ConfigError(f"{dataset_name} 类别目录不完整: {class_dir}")
            elif not class_dir.is_dir():
                raise ConfigError(f"{dataset_name} 类别目录不存在: {class_dir}")

    return {
        "python_executable": python_executable,
        "source_directory": source_directory,
        "entrypoint": entrypoint,
        "dataset_name": dataset_name,
        "dataset_loader": profile["loader"],
        "dataset_root": dataset_root,
        "classes": classes,
        "gpu_index": gpu_index,
        "epochs": epochs,
        "batch_size": batch_size,
        "num_workers": num_workers,
        "resize": resize,
        "image_size": image_size,
        "seed": seed,
        "eval_every": eval_every,
        "learning_rate": float(learning_rate),
        "output_root": output_root,
    }


def build_command(values: dict[str, Any], run_directory: Path) -> list[str]:
    command = [
        str(values["python_executable"]),
        str(values["entrypoint"]),
        "--results_path",
        str(run_directory / "artifacts"),
        "--device_name",
        "cuda:0",
        "--seed",
        str(values["seed"]),
        "--log_project",
        "phase0",
        "--log_group",
        f"pbas-{values['dataset_loader']}",
        "--run_name",
        run_directory.name,
        "--test",
        "ckpt",
        "net",
        "-b",
        "wideresnet50",
        "-le",
        "layer2",
        "-le",
        "layer3",
        "--pretrain_embed_dimension",
        "1536",
        "--target_embed_dimension",
        "1536",
        "--patchsize",
        "3",
        "--meta_epochs",
        str(values["epochs"]),
        "--eval_epochs",
        str(values["eval_every"]),
        "--dsc_layers",
        "2",
        "--dsc_hidden",
        "1024",
        "--pre_proj",
        "1",
        "--k",
        "0.25",
        "--lr",
        str(values["learning_rate"]),
        "dataset",
        "--batch_size",
        str(values["batch_size"]),
        "--num_workers",
        str(values["num_workers"]),
        "--resize",
        str(values["resize"]),
        "--imagesize",
        str(values["image_size"]),
    ]
    for class_name in values["classes"]:
        command.extend(["-d", class_name])
    command.extend([values["dataset_loader"], str(values["dataset_root"])])
    return command


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def list_artifacts(run_directory: Path) -> list[dict[str, Any]]:
    artifacts = []
    for path in sorted(run_directory.rglob("*")):
        if path.is_file() and path.name not in {"manifest.json"}:
            artifacts.append(
                {
                    "path": str(path.relative_to(run_directory)),
                    "size_bytes": path.stat().st_size,
                }
            )
    return artifacts


def stream_process(
    command: list[str],
    env: dict[str, str],
    cwd: Path,
    log_path: Path,
    runtime_path: Path,
) -> int:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    process_pgid = os.getpgid(process.pid)
    write_json(
        runtime_path,
        {
            "worker_pid": os.getpid(),
            "worker_pgid": os.getpgrp(),
            "process_pid": process.pid,
            "process_pgid": process_pgid,
            "started_at": utc_now().isoformat(),
        },
    )

    previous_handlers = {}

    def forward_signal(signum, _frame):
        try:
            os.killpg(process_pgid, signum)
        except ProcessLookupError:
            pass

    for signum in (signal.SIGTERM, signal.SIGINT):
        previous_handlers[signum] = signal.signal(signum, forward_signal)
    try:
        with log_path.open("w", encoding="utf-8") as log_file:
            assert process.stdout is not None
            for line in process.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                log_file.write(line)
                log_file.flush()
        return process.wait()
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


def run(config_path: Path, run_id: str | None, check_only: bool) -> int:
    config = load_config(config_path)
    values = validate_config(config)
    if check_only:
        print("配置与服务器路径检查通过")
        print(f"PBAS 入口: {values['entrypoint']}")
        print(f"Conda Python: {values['python_executable']}")
        print(f"{values['dataset_name']} 类别: {', '.join(values['classes'])}")
        print(f"GPU: {values['gpu_index']}；epochs: {values['epochs']}")
        return 0

    if run_id is None:
        run_id = (
            f"pbas-{values['dataset_loader']}-"
            f"{utc_now().strftime('%Y%m%dT%H%M%SZ')}"
        )
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ConfigError("run-id 只能包含字母、数字、点、下划线和连字符，最长 80 字符")

    output_root: Path = values["output_root"]
    output_root.mkdir(parents=True, exist_ok=True)
    run_directory = output_root / run_id
    try:
        run_directory.mkdir(mode=0o750)
    except FileExistsError as exc:
        raise ConfigError(f"运行目录已存在，拒绝覆盖: {run_directory}") from exc
    (run_directory / "artifacts").mkdir()
    (run_directory / "work").mkdir()

    config_snapshot = run_directory / "config.json"
    shutil.copyfile(config_path, config_snapshot)
    command = build_command(values, run_directory)
    write_json(run_directory / "command.json", {"argv": command})

    started_at = utc_now()
    manifest = {
        "protocol_version": "phase0-v1",
        "run_id": run_id,
        "algorithm": "PBAS",
        "dataset": values["dataset_name"],
        "classes": values["classes"],
        "gpu_index": values["gpu_index"],
        "status": "RUNNING",
        "worker_pid": os.getpid(),
        "worker_pgid": os.getpgrp(),
        "process_pid": None,
        "process_pgid": None,
        "started_at": started_at.isoformat(),
        "finished_at": None,
        "exit_code": None,
        "artifacts": [],
    }
    write_json(run_directory / "manifest.json", manifest)

    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = str(values["gpu_index"])
    env["PYTHONUNBUFFERED"] = "1"
    start_monotonic = time.monotonic()
    runtime_path = run_directory / "runtime.json"
    exit_code = stream_process(
        command,
        env,
        run_directory / "work",
        run_directory / "raw.log",
        runtime_path,
    )
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    finished_at = utc_now()
    manifest.update(
        {
            **runtime,
            "status": "SUCCEEDED" if exit_code == 0 else "FAILED",
            "finished_at": finished_at.isoformat(),
            "duration_seconds": round(time.monotonic() - start_monotonic, 3),
            "exit_code": exit_code,
            "artifacts": list_artifacts(run_directory),
        }
    )
    write_json(run_directory / "manifest.json", manifest)
    print(f"训练结束: {manifest['status']} (exit_code={exit_code})")
    print(f"运行目录: {run_directory}")
    return exit_code


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PBAS 工业异常数据集训练适配器")
    parser.add_argument("--config", required=True, type=Path, help="固定 JSON 配置路径")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="仅检查配置与服务器路径")
    mode.add_argument("--run", action="store_true", help="执行 5-10 epoch 训练")
    parser.add_argument("--run-id", help="可选的唯一运行编号")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
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
