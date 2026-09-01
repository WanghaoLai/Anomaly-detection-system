#!/usr/bin/env python3
"""Phase 6 本地上传安全/解析隔离压力与长稳测试，不调用外部模型服务。"""

from __future__ import annotations

import argparse
import asyncio
import json
import resource
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from services.rag.document import (  # noqa: E402
    LocalClamAvScanner,
    ProcessIsolatedDocumentParser,
    UploadSecurityPolicy,
    validate_upload_content,
)
from settings import AI_CONFIG  # noqa: E402


PAYLOAD = b"# Phase 6 local fixture\n\nSafe local parsing workload.\n"
POLICY = UploadSecurityPolicy(
    max_archive_entries=int(AI_CONFIG["rag_archive_max_entries"]),
    max_archive_uncompressed_bytes=int(
        AI_CONFIG["rag_archive_max_uncompressed_bytes"]
    ),
    max_archive_compression_ratio=float(
        AI_CONFIG["rag_archive_max_compression_ratio"]
    ),
)


def _parser_payload() -> dict:
    return {
        "file_bytes": PAYLOAD,
        "filename": "phase6-local.md",
        "document_id": None,
        "source": None,
        "chunk_tokens": int(AI_CONFIG["rag_chunk_tokens"]),
        "overlap_tokens": int(AI_CONFIG["rag_overlap_tokens"]),
        "ingestion_schema_version": "phase6-local-benchmark-v1",
        "ocr_min_chars": int(AI_CONFIG["rag_ocr_min_chars"]),
        "ocr_min_chars_per_page": int(AI_CONFIG["rag_ocr_min_chars_per_page"]),
        "ocr": {"enabled": False},
    }


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _rss_snapshot() -> dict:
    self_usage = resource.getrusage(resource.RUSAGE_SELF)
    child_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    factor = 1 if sys.platform == "darwin" else 1024
    return {
        "self_max_rss_bytes": int(self_usage.ru_maxrss) * factor,
        "children_max_rss_bytes": int(child_usage.ru_maxrss) * factor,
    }


def _scanner() -> LocalClamAvScanner:
    return LocalClamAvScanner(
        str(AI_CONFIG["rag_clamav_path"]),
        expected_version=str(AI_CONFIG["rag_clamav_version"]),
        database_path=str(AI_CONFIG["rag_clamav_database_path"]),
        certs_path=str(AI_CONFIG["rag_clamav_certs_path"]),
        timeout_seconds=float(AI_CONFIG["rag_clamav_timeout_seconds"]),
        max_signature_age_seconds=float(
            AI_CONFIG["rag_clamav_max_signature_age_seconds"]
        ),
    )


def _parser() -> ProcessIsolatedDocumentParser:
    return ProcessIsolatedDocumentParser(
        wall_timeout_seconds=float(AI_CONFIG["rag_parser_wall_timeout_seconds"]),
        memory_limit_bytes=int(AI_CONFIG["rag_parser_memory_limit_bytes"]),
        cpu_limit_seconds=int(AI_CONFIG["rag_parser_cpu_limit_seconds"]),
    )


async def _one_request(parser, semaphore) -> float:
    started = time.perf_counter()
    validate_upload_content(
        PAYLOAD, "phase6-local.md", "text/markdown", policy=POLICY
    )
    async with semaphore:
        result = await asyncio.to_thread(parser.prepare, **_parser_payload())
    if not result.get("chunks"):
        raise RuntimeError("隔离解析没有生成 Node")
    return (time.perf_counter() - started) * 1000


async def run_load(concurrency_levels: list[int]) -> dict:
    parser = _parser()
    # 与生产 AI_RAG_INGESTION_CONCURRENCY=1 保持一致，测量排队而非绕过资源门禁。
    semaphore = asyncio.Semaphore(int(AI_CONFIG["rag_ingestion_concurrency"]))
    scanner_started = time.perf_counter()
    await asyncio.to_thread(_scanner().scan, PAYLOAD, "phase6-local.md")
    scanner_ms = (time.perf_counter() - scanner_started) * 1000
    waves = []
    for concurrency in concurrency_levels:
        started = time.perf_counter()
        results = await asyncio.gather(
            *[_one_request(parser, semaphore) for _ in range(concurrency)],
            return_exceptions=True,
        )
        elapsed = time.perf_counter() - started
        errors = [type(value).__name__ for value in results if isinstance(value, BaseException)]
        durations = [float(value) for value in results if not isinstance(value, BaseException)]
        wave = {
            "concurrency": concurrency,
            "requests": len(results),
            "successes": len(durations),
            "errors": len(errors),
            "error_types": sorted(set(errors)),
            "elapsed_seconds": round(elapsed, 3),
            "throughput_rps": round(len(durations) / elapsed, 4) if elapsed else 0.0,
            "latency_ms": {
                "p50": round(_percentile(durations, 0.50), 2),
                "p95": round(_percentile(durations, 0.95), 2),
                "p99": round(_percentile(durations, 0.99), 2),
                "max": round(max(durations), 2) if durations else 0.0,
            },
            "rss": _rss_snapshot(),
        }
        waves.append(wave)
        print(json.dumps({"event": "load_wave", **wave}, ensure_ascii=False), flush=True)
    return {
        "mode": "local_load",
        "external_model_calls": 0,
        "parser_concurrency": int(AI_CONFIG["rag_ingestion_concurrency"]),
        "clamav_clean_scan_ms": round(scanner_ms, 2),
        "waves": waves,
        "passed": all(wave["errors"] == 0 for wave in waves),
    }


async def run_soak(duration_seconds: int, heartbeat_seconds: int) -> dict:
    parser = _parser()
    semaphore = asyncio.Semaphore(int(AI_CONFIG["rag_ingestion_concurrency"]))
    started = time.monotonic()
    deadline = started + duration_seconds
    next_parser = started
    next_scan = started
    next_heartbeat = started
    preflight_count = 0
    parser_count = 0
    scan_count = 0
    errors: list[str] = []
    latencies: list[float] = []
    while time.monotonic() < deadline:
        loop_started = time.perf_counter()
        try:
            validate_upload_content(
                PAYLOAD, "phase6-local.md", "text/markdown", policy=POLICY
            )
            preflight_count += 1
            now = time.monotonic()
            if now >= next_parser:
                latencies.append(await _one_request(parser, semaphore))
                parser_count += 1
                next_parser = now + 60
            if now >= next_scan:
                await asyncio.to_thread(_scanner().scan, PAYLOAD, "phase6-local.md")
                scan_count += 1
                next_scan = now + 600
        except BaseException as exc:
            errors.append(type(exc).__name__)
        now = time.monotonic()
        if now >= next_heartbeat:
            print(json.dumps({
                "event": "soak_heartbeat",
                "elapsed_seconds": round(now - started, 1),
                "preflight_count": preflight_count,
                "parser_count": parser_count,
                "scan_count": scan_count,
                "errors": len(errors),
                "rss": _rss_snapshot(),
            }, ensure_ascii=False), flush=True)
            next_heartbeat = now + heartbeat_seconds
        await asyncio.sleep(max(0.0, 1.0 - (time.perf_counter() - loop_started)))
    elapsed = time.monotonic() - started
    return {
        "mode": "local_soak",
        "external_model_calls": 0,
        "target_seconds": duration_seconds,
        "elapsed_seconds": round(elapsed, 2),
        "preflight_count": preflight_count,
        "parser_count": parser_count,
        "clamav_scan_count": scan_count,
        "errors": len(errors),
        "error_types": sorted(set(errors)),
        "parser_latency_ms": {
            "p50": round(_percentile(latencies, 0.50), 2),
            "p95": round(_percentile(latencies, 0.95), 2),
            "p99": round(_percentile(latencies, 0.99), 2),
        },
        "rss": _rss_snapshot(),
        "passed": not errors and elapsed >= duration_seconds,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("load", "soak"), required=True)
    parser.add_argument("--concurrency", default="10,30,50")
    parser.add_argument("--duration-seconds", type=int, default=7200)
    parser.add_argument("--heartbeat-seconds", type=int, default=300)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "load":
        levels = [int(value) for value in args.concurrency.split(",") if value]
        result = asyncio.run(run_load(levels))
    else:
        result = asyncio.run(run_soak(args.duration_seconds, args.heartbeat_seconds))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"event": "complete", **result}, ensure_ascii=False), flush=True)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
