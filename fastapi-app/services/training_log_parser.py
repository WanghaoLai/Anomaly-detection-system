"""训练日志公共模型、安全清洗，以及 PBAS 原生日志解析器。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


ANSI_ESCAPE_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
EPOCH_RE = re.compile(
    r"epoch:(?P<epoch>\d+)\s+"
    r"sl:(?P<segmentation_loss>[-+0-9.eE]+)\s+"
    r"bl:(?P<binary_loss>[-+0-9.eE]+).*?"
    r"sample:(?P<sample>\d+)"
)
EVALUATION_RE = re.compile(
    r"IAUC:(?P<image>[-+0-9.]+)\([^)]*\)\s+"
    r"PAUC:(?P<pixel>[-+0-9.]+)\([^)]*\)\s+"
    r"E:(?P<evaluated_epoch>\d+)\([^)]*\)"
)
EPOCH_PROGRESS_RE = re.compile(r"(?P<percent>\d+)%\|[^|]*\|\s*(?P<done>\d+)/(?P<total>\d+)")
FINAL_METRICS_RE = re.compile(
    r"image_auroc:(?P<image_auroc>[-+0-9.]+)\s+"
    r"image_ap:(?P<image_ap>[-+0-9.]+)\s+"
    r"pixel_auroc:(?P<pixel_auroc>[-+0-9.]+)\s+"
    r"pixel_ap:(?P<pixel_ap>[-+0-9.]+)\s+"
    r"pixel_pro:(?P<pixel_pro>[-+0-9.]+)\s+"
    r"best_epoch:(?P<best_epoch>\d+)"
)


@dataclass
class ParsedTrainingLine:
    content: str
    stream: str = "STDOUT"
    persist: bool = True
    progress_percent: float | None = None
    current_epoch: int | None = None
    total_epochs: int | None = None
    metrics: list[tuple[str, float, int | None]] = field(default_factory=list)


def clean_log_line(raw: str) -> str:
    value = ANSI_ESCAPE_RE.sub("", raw)
    value = value.replace("\x00", "").replace("\b", "")
    return value.strip()


def parse_training_line(raw: str) -> ParsedTrainingLine | None:
    line = clean_log_line(raw)
    if not line:
        return None

    is_error = (
        "ERROR" in line
        or "RuntimeError:" in line
        or "OSError:" in line
        or "CUDA out of memory" in line
        or "No space left on device" in line
        or "Traceback (most recent call last)" in line
        or line.startswith(("配置错误:", "执行错误:"))
    )
    stream = "ERROR" if is_error else "STDOUT"
    parsed = ParsedTrainingLine(content=line[:4000], stream=stream)

    final_match = FINAL_METRICS_RE.search(line)
    if final_match:
        best_epoch = int(final_match.group("best_epoch"))
        for name in (
            "image_auroc", "image_ap", "pixel_auroc", "pixel_ap", "pixel_pro"
        ):
            parsed.metrics.append(
                (name, float(final_match.group(name)) / 100.0, best_epoch)
            )
        parsed.progress_percent = 100.0
        return parsed

    epoch_match = EPOCH_RE.search(line)
    if epoch_match:
        parsed.stream = "PROGRESS"
        epoch = int(epoch_match.group("epoch"))
        evaluation_match = EVALUATION_RE.search(line)
        if evaluation_match and int(evaluation_match.group("evaluated_epoch")) == epoch:
            parsed.metrics.extend([
                (
                    "train/segmentation_loss",
                    float(epoch_match.group("segmentation_loss")),
                    epoch + 1,
                ),
                (
                    "train/binary_loss",
                    float(epoch_match.group("binary_loss")),
                    epoch + 1,
                ),
                (
                    "eval/image_auroc",
                    float(evaluation_match.group("image")) / 100.0,
                    epoch + 1,
                ),
                (
                    "eval/pixel_auroc",
                    float(evaluation_match.group("pixel")) / 100.0,
                    epoch + 1,
                ),
            ])
            parsed.persist = True
        else:
            # PBAS 每个 batch 都重绘同一行；后台每轮同步只保留最后一条。
            parsed.persist = False

        progress_match = EPOCH_PROGRESS_RE.search(line)
        if progress_match:
            parsed.progress_percent = float(progress_match.group("percent"))
            parsed.current_epoch = int(progress_match.group("done"))
            parsed.total_epochs = int(progress_match.group("total"))
        return parsed

    if "Inferring...:" in line or re.search(r"\d+(?:\.\d+)?[kM]?/132M", line):
        parsed.stream = "PROGRESS"
        parsed.persist = "100%" in line
        return parsed

    parsed.persist = (
        line.startswith(("INFO:", "WARNING:", "ERROR:"))
        or "Dataset------" in line
        or line.startswith(("mean_fluctuation:", "训练结束:", "运行目录:"))
        or is_error
    )
    return parsed
