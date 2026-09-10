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


def _to_float(value: str) -> float | None:
    # 数值正则的字符类允许 "..."、"--"、"1.2.3" 等非浮点串（tqdm
    # 重绘交错、日志损坏都会产生），转换失败必须降级而不是抛异常——
    # 单行解析失败若向上抛，会被计入 reconcile 三振把健康任务判 LOST。
    try:
        return float(value)
    except ValueError:
        return None


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
        final_values = {
            name: _to_float(final_match.group(name))
            for name in ("image_auroc", "image_ap", "pixel_auroc", "pixel_ap", "pixel_pro")
        }
        if all(value is not None for value in final_values.values()):
            for name, value in final_values.items():
                parsed.metrics.append((name, value / 100.0, best_epoch))
            parsed.progress_percent = 100.0
            return parsed
        # 数值字段畸形：降级为普通文本行，绝不让单行杀死解析循环。

    epoch_match = EPOCH_RE.search(line)
    if epoch_match:
        parsed.stream = "PROGRESS"
        epoch = int(epoch_match.group("epoch"))
        evaluation_match = EVALUATION_RE.search(line)
        segmentation_loss = _to_float(epoch_match.group("segmentation_loss"))
        binary_loss = _to_float(epoch_match.group("binary_loss"))
        image_auroc = (
            _to_float(evaluation_match.group("image")) if evaluation_match else None
        )
        pixel_auroc = (
            _to_float(evaluation_match.group("pixel")) if evaluation_match else None
        )
        evaluation_aligned = (
            evaluation_match is not None
            and int(evaluation_match.group("evaluated_epoch")) == epoch
        )
        if (
            evaluation_aligned
            and None not in (segmentation_loss, binary_loss, image_auroc, pixel_auroc)
        ):
            parsed.metrics.extend([
                (
                    "train/segmentation_loss",
                    segmentation_loss,
                    epoch + 1,
                ),
                (
                    "train/binary_loss",
                    binary_loss,
                    epoch + 1,
                ),
                (
                    "eval/image_auroc",
                    image_auroc / 100.0,
                    epoch + 1,
                ),
                (
                    "eval/pixel_auroc",
                    pixel_auroc / 100.0,
                    epoch + 1,
                ),
            ])
            parsed.persist = True
        else:
            # PBAS 每个 batch 都重绘同一行；后台每轮同步只保留最后一条。
            # 数值畸形的行同样按普通进度行处理，不产出指标。
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
