"""文档入库前的纯编排，不依赖向量库或模型厂商。"""

from __future__ import annotations

import asyncio
import multiprocessing
import queue
import sys
import time

from ..core.contracts import DocumentLoader, DocumentPreprocessor, NodeParser


class AsyncIngestionExecutor:
    """对 CPU/同步 SDK 入库链路进行有界异步调度。"""

    def __init__(self, max_concurrency: int = 1):
        if max_concurrency <= 0:
            raise ValueError("max_concurrency 必须大于 0")
        self.max_concurrency = int(max_concurrency)
        self._semaphore = asyncio.Semaphore(self.max_concurrency)

    async def run(self, callable_, /, *args, **kwargs):
        async with self._semaphore:
            return await asyncio.to_thread(callable_, *args, **kwargs)


def _apply_parser_resource_limits(memory_limit_bytes: int, cpu_limit_seconds: int) -> None:
    """在子进程内应用资源限制；macOS 不宣称提供可靠的 RLIMIT_AS。"""
    try:
        import resource
    except ImportError:
        return
    if cpu_limit_seconds > 0:
        resource.setrlimit(
            resource.RLIMIT_CPU,
            (int(cpu_limit_seconds), int(cpu_limit_seconds) + 1),
        )
    if sys.platform.startswith("linux") and memory_limit_bytes > 0:
        resource.setrlimit(
            resource.RLIMIT_AS,
            (int(memory_limit_bytes), int(memory_limit_bytes)),
        )


def _isolated_prepare_worker(result_queue, payload: dict) -> None:
    """独立进程入口；只解析/分块，不连接向量库或外部模型服务。"""
    try:
        _apply_parser_resource_limits(
            int(payload["memory_limit_bytes"]),
            int(payload["cpu_limit_seconds"]),
        )
        from .loading import prepare_document_worker_payload

        result_queue.put(("ok", prepare_document_worker_payload(payload)))
    except BaseException as exc:
        result_queue.put(("error", type(exc).__name__, str(exc)[:1000]))


class ProcessIsolatedDocumentParser:
    """每次候选解析使用可强制终止的独立 spawn 进程。"""

    def __init__(
        self,
        *,
        wall_timeout_seconds: float = 120.0,
        memory_limit_bytes: int = 1024 * 1024 * 1024,
        cpu_limit_seconds: int = 60,
        worker_target=_isolated_prepare_worker,
    ) -> None:
        if wall_timeout_seconds <= 0:
            raise ValueError("Parser Wall Timeout 必须大于 0")
        self.wall_timeout_seconds = float(wall_timeout_seconds)
        self.memory_limit_bytes = int(memory_limit_bytes)
        self.cpu_limit_seconds = int(cpu_limit_seconds)
        self.worker_target = worker_target

    def prepare(self, **payload) -> dict:
        context = multiprocessing.get_context("spawn")
        result_queue = context.Queue(maxsize=1)
        process = context.Process(
            target=self.worker_target,
            args=(result_queue, {
                **payload,
                "memory_limit_bytes": self.memory_limit_bytes,
                "cpu_limit_seconds": self.cpu_limit_seconds,
            }),
            name="rag-document-parser",
            daemon=True,
        )
        process.start()
        deadline = time.monotonic() + self.wall_timeout_seconds
        message = None
        try:
            while message is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"文档解析超过 {self.wall_timeout_seconds:g} 秒安全上限"
                    )
                try:
                    message = result_queue.get(timeout=min(0.2, remaining))
                except queue.Empty:
                    if not process.is_alive():
                        raise RuntimeError("文档解析进程异常退出")
            process.join(timeout=2)
        finally:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
                if process.is_alive() and hasattr(process, "kill"):
                    process.kill()
                    process.join(timeout=2)
            result_queue.close()
        if not message or message[0] != "ok":
            error_name = message[1] if message else "ParserError"
            error_text = message[2] if message else "文档解析失败"
            if error_name == "ValueError":
                raise ValueError(error_text)
            raise RuntimeError(f"文档解析失败: {error_name}")
        return dict(message[1])


class DocumentIngestionPipeline:
    def __init__(
        self,
        loader: DocumentLoader,
        preprocessor: DocumentPreprocessor,
        node_parser: NodeParser,
    ):
        self.loader = loader
        self.preprocessor = preprocessor
        self.node_parser = node_parser

    def prepare(self, file_bytes: bytes, filename: str) -> dict:
        loaded = self.loader.load(file_bytes, filename)
        document, diagnostics = self.preprocessor.process(loaded)
        nodes = list(self.node_parser.parse(document))
        if not nodes:
            raise ValueError("文档分块后无有效内容")
        chunks = [{"content": node.text, **dict(node.metadata)} for node in nodes]
        diagnostics = dict(diagnostics)
        diagnostics["chunk_count"] = len(chunks)
        diagnostics["average_chunk_tokens"] = round(
            sum(int(chunk.get("token_count") or 0) for chunk in chunks) / len(chunks),
            1,
        )
        return {
            "filename": document.metadata["filename"],
            "extension": document.metadata["extension"],
            "markdown": document.text,
            "chunks": chunks,
            "diagnostics": diagnostics,
        }


__all__ = [
    "AsyncIngestionExecutor",
    "DocumentIngestionPipeline",
    "ProcessIsolatedDocumentParser",
]
