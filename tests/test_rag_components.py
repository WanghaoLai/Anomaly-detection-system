import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from services.rag.core.contracts import Document, Node  # noqa: E402
from services.rag.answering.prompting import (  # noqa: E402
    HistoryAwareQueryTransformer,
    NumberedContextBuilder,
    PromptBuilder,
    RAGGenerationPipeline,
)
from services.rag.document.pipeline import DocumentIngestionPipeline  # noqa: E402
from services.rag.search.retrieval import HybridResultSelector, RetrievalPolicy  # noqa: E402


class _Loader:
    def load(self, file_bytes, filename):
        return Document(file_bytes.decode(), {"filename": filename, "extension": ".md"})


class _Preprocessor:
    def process(self, document):
        return Document(document.text.strip(), document.metadata), {"cleaned": True}


class _Parser:
    def parse(self, document):
        return [Node(document.text, {"token_count": 2, "start": 0, "end": len(document.text)})]


class _Generator:
    async def chat(self, messages, system_prompt=None):
        return messages[-1]["content"]

    async def chat_stream(self, messages, system_prompt=None):
        yield messages[-1]["content"]


class RagComponentTests(unittest.TestCase):
    def test_ingestion_pipeline_composes_replaceable_stages(self):
        result = DocumentIngestionPipeline(
            _Loader(), _Preprocessor(), _Parser()
        ).prepare(b"  body  ", "manual.md")

        self.assertEqual(result["markdown"], "body")
        self.assertEqual(result["chunks"][0]["content"], "body")
        self.assertEqual(result["diagnostics"]["chunk_count"], 1)

    def test_hybrid_selector_keeps_dense_and_exact_lexical_nodes(self):
        selector = HybridResultSelector(RetrievalPolicy(
            candidate_k=8,
            final_k=4,
            score_threshold=0.2,
            hybrid_enabled=True,
            lexical_min_score=0.08,
        ))
        selected, stats = selector.select_hybrid(
            "watch -n 2 nvidia-smi",
            [{"content": "GPU help", "doc_id": "dense", "chunk_index": 0, "score": 0.8}],
            [{"content": "watch -n 2 nvidia-smi", "doc_id": "exact", "chunk_index": 1}],
        )

        self.assertEqual(stats["mode"], "hybrid")
        self.assertEqual({item["doc_id"] for item in selected}, {"dense", "exact"})

    def test_query_context_prompt_and_generator_are_independent(self):
        query = HistoryAwareQueryTransformer(1).transform(
            "那 macOS 呢？",
            [{"role": "user", "content": "Windows 如何配置？"}],
        )
        context, _ = NumberedContextBuilder(100).build([
            {"content": "配置正文", "filename": "manual.md", "score": 0.9}
        ])
        pipeline = RAGGenerationPipeline(_Generator(), PromptBuilder(), "system")

        answer = self.run_async(pipeline.generate([], query, context))

        self.assertIn("历史问题：Windows 如何配置？", answer)
        self.assertIn("[K1] 来源：manual.md", answer)

    def run_async(self, awaitable):
        import asyncio
        return asyncio.run(awaitable)


if __name__ == "__main__":
    unittest.main()
