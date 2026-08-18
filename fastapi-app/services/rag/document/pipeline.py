"""文档入库前的纯编排，不依赖向量库或模型厂商。"""

from __future__ import annotations

import asyncio

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


__all__ = ["AsyncIngestionExecutor", "DocumentIngestionPipeline"]
