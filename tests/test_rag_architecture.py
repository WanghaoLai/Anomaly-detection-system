import inspect
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


class RagArchitectureRefactorTests(unittest.TestCase):
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

    def test_implementations_are_owned_by_new_modules(self):
        ownership = (
            (approx_token_len, "services.rag.document.splitting"),
            (preprocess_pdf_markdown, "services.rag.document.loading"),
            (AuthorizedRetrievalPipeline, "services.rag.search.pipeline"),
            (LLMTimeoutError, "services.rag.answering.llm_types"),
        )
        for symbol, expected_module in ownership:
            self.assertEqual(symbol.__module__, expected_module)


if __name__ == "__main__":
    unittest.main()
