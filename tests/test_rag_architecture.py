import inspect
import importlib
import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

import services.knowledge_service as knowledge_module  # noqa: E402
from services.llm_service import (  # noqa: E402
    LLMGenerationError as FacadeGenerationError,
    LLMTimeoutError as FacadeTimeoutError,
)
from services.rag.answering import (  # noqa: E402
    LLMGenerationError,
    LLMTimeoutError,
)
from services.rag.document import (  # noqa: E402
    approx_token_len,
    chunk_paragraphs,
    preprocess_pdf_markdown,
    split_paragraphs_with_headings,
)
from services.rag.search import (  # noqa: E402
    AuthorizedRetrievalPipeline,
    SearchRuntimeConfig,
)
from scripts.check_rag_legacy_imports import scan_production_imports  # noqa: E402


class RagArchitectureRefactorTests(unittest.TestCase):
    LEGACY_SHIMS = (
        "contracts.py", "access.py", "loaders.py", "splitters.py",
        "ingestion.py", "artifacts.py", "llamaindex_parser.py",
        "embeddings.py", "vector_store.py", "retrieval.py", "lexical.py",
        "reranking.py", "context.py", "generation.py", "grounding.py",
        "audit.py", "sse.py",
    )

    def test_knowledge_legacy_helpers_are_compatibility_aliases(self):
        self.assertIs(knowledge_module._approx_token_len, approx_token_len)
        self.assertIs(
            knowledge_module._split_paragraphs_with_headings,
            split_paragraphs_with_headings,
        )
        self.assertIs(
            knowledge_module._preprocess_pdf_markdown,
            preprocess_pdf_markdown,
        )
        self.assertIs(knowledge_module._chunk_paragraphs, chunk_paragraphs)

    def test_knowledge_facade_no_longer_defines_duplicate_algorithms(self):
        source = inspect.getsource(knowledge_module)
        self.assertNotIn("def _preprocess_pdf_markdown(", source)
        self.assertNotIn("def _split_paragraphs_with_headings(", source)
        self.assertNotIn("def _chunk_paragraphs(", source)

    def test_llm_error_contract_is_model_independent_and_compatible(self):
        self.assertIs(FacadeTimeoutError, LLMTimeoutError)
        self.assertIs(FacadeGenerationError, LLMGenerationError)

    def test_authorized_search_pipeline_has_explicit_runtime_contract(self):
        self.assertTrue(inspect.iscoroutinefunction(
            AuthorizedRetrievalPipeline.retrieve
        ))
        fields = set(SearchRuntimeConfig.__dataclass_fields__)
        self.assertIn("acl_pushdown_enabled", fields)
        self.assertIn("dense_k", fields)
        self.assertIn("final_k", fields)

    def test_legacy_flat_modules_are_pure_compatibility_shims(self):
        rag_root = BACKEND_DIR / "services" / "rag"
        for filename in self.LEGACY_SHIMS:
            source = (rag_root / filename).read_text(encoding="utf-8")
            definitions = [
                line for line in source.splitlines()
                if line.startswith(("class ", "def ", "async def "))
            ]
            self.assertEqual(definitions, [], filename)
            self.assertIn("_reexport(globals(), _implementation)", source)

    def test_old_and_new_imports_return_same_objects(self):
        mappings = (
            ("services.rag.contracts", "services.rag.core.contracts", "Document"),
            ("services.rag.loaders", "services.rag.document.loading", "MarkItDownDocumentLoader"),
            ("services.rag.retrieval", "services.rag.search.retrieval", "HybridResultSelector"),
            ("services.rag.context", "services.rag.answering.context", "ContextPacker"),
            ("services.rag.grounding", "services.rag.answering.grounding", "GroundedAnswerValidator"),
            ("services.rag.sse", "services.rag.operations.sse", "encode_sse"),
        )
        for old_path, new_path, symbol in mappings:
            old_module = importlib.import_module(old_path)
            new_module = importlib.import_module(new_path)
            self.assertIs(
                getattr(old_module, symbol),
                getattr(new_module, symbol),
                old_path,
            )

    def test_writer_legacy_module_alias_preserves_fault_injection_path(self):
        old_module = importlib.import_module("services.rag.llamaindex_indexing")
        new_module = importlib.import_module("services.rag.indexing.writer")
        self.assertIs(old_module, new_module)

    def test_implementations_are_owned_by_new_modules(self):
        ownership = (
            (approx_token_len, "services.rag.document.splitting"),
            (preprocess_pdf_markdown, "services.rag.document.loading"),
            (AuthorizedRetrievalPipeline, "services.rag.search.pipeline"),
            (LLMTimeoutError, "services.rag.answering.llm_types"),
        )
        for symbol, expected_module in ownership:
            self.assertEqual(symbol.__module__, expected_module)

    def test_production_code_has_no_legacy_flat_imports(self):
        self.assertEqual(scan_production_imports(), [])

    def test_paper_provider_types_do_not_leak_into_core_contracts(self):
        core_source = (
            BACKEND_DIR / "services" / "rag" / "core" / "contracts.py"
        ).read_text(encoding="utf-8").casefold()
        self.assertNotIn("docling", core_source)
        self.assertNotIn("grobid", core_source)


if __name__ == "__main__":
    unittest.main()
