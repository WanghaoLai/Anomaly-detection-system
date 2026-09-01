import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from llama_index.core.schema import TextNode  # noqa: E402
from services.knowledge_service import KnowledgeService  # noqa: E402
from services.rag.core import Document  # noqa: E402
from services.rag.document import (  # noqa: E402
    DefaultDocumentPreprocessor,
    KnowledgeArtifactRepository,
    LocalTesseractPdfOcr,
    MarkItDownDocumentLoader,
    PdfOcrResult,
    RELEASE_SMOKE_SCHEMA_VERSION,
)
from evaluate_rag_phase4_release_smoke import _validated_cases  # noqa: E402
from services.rag.indexing import (  # noqa: E402
    CachedNodeEmbedder,
    LlamaIndexChromaIndexWriter,
    SQLiteEmbeddingCache,
)


class FakeAdapter:
    embed_batch_size = 2

    def __init__(self):
        self.calls = 0

    def get_text_embedding_batch(self, texts):
        self.calls += 1
        return [[1.0, float(index + 1)] for index, _ in enumerate(texts)]

    def backend_metrics_snapshot(self):
        return {"api_calls": self.calls, "retries": 0}


def embedder(path, adapter=None, **overrides):
    adapter = adapter or FakeAdapter()
    values = {
        "provider": "test-provider",
        "model": "test-model",
        "schema_version": "test-schema-v1",
        "normalized": False,
        **overrides,
    }
    return CachedNodeEmbedder(
        embedding_adapter=adapter,
        cache=SQLiteEmbeddingCache(path),
        **values,
    ), adapter


class EmbeddingCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cache_path = Path(self.temp.name) / "document_embeddings.sqlite3"
        self.nodes = [
            TextNode(id_="n1", text="节点一"),
            TextNode(id_="n2", text="节点二"),
        ]

    def tearDown(self):
        self.temp.cleanup()

    def test_second_build_reuses_all_vectors_without_provider_call(self):
        coordinator, adapter = embedder(self.cache_path)

        first, first_stats = coordinator.embed(
            self.nodes, expected_dimension=None
        )
        second, second_stats = coordinator.embed(
            self.nodes, expected_dimension=2
        )

        self.assertEqual(first, second)
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(first_stats.generated_embeddings, 2)
        self.assertEqual(second_stats.cache_hits, 2)
        self.assertEqual(second_stats.generated_embeddings, 0)
        self.assertEqual(second_stats.embedding_api_calls, 0)

    def test_text_model_schema_and_dimension_are_part_of_cache_identity(self):
        coordinator, adapter = embedder(self.cache_path)
        coordinator.embed(self.nodes, expected_dimension=None)

        changed_text = [TextNode(id_="n1", text="节点一已修改")]
        _, text_stats = coordinator.embed(changed_text, expected_dimension=2)
        other_model, _ = embedder(
            self.cache_path, adapter, model="test-model-v2"
        )
        _, model_stats = other_model.embed(self.nodes, expected_dimension=2)
        other_schema, _ = embedder(
            self.cache_path, adapter, schema_version="test-schema-v2"
        )
        _, schema_stats = other_schema.embed(self.nodes, expected_dimension=2)
        calls_before_dimension_change = adapter.calls
        with self.assertRaisesRegex(RuntimeError, "维度"):
            coordinator.embed(self.nodes, expected_dimension=3)

        self.assertEqual(text_stats.cache_misses, 1)
        self.assertEqual(model_stats.cache_misses, 2)
        self.assertEqual(schema_stats.cache_misses, 2)
        self.assertEqual(adapter.calls, calls_before_dimension_change + 1)

    def test_corrupt_cache_entry_is_ignored_and_regenerated(self):
        coordinator, adapter = embedder(self.cache_path)
        coordinator.embed(self.nodes, expected_dimension=None)
        with closing(sqlite3.connect(self.cache_path)) as connection:
            connection.execute(
                "UPDATE document_embeddings SET vector_blob = ?",
                (b"not-a-vector",),
            )
            connection.commit()

        _, stats = coordinator.embed(self.nodes, expected_dimension=2)

        self.assertEqual(stats.cache_invalid_entries, 2)
        self.assertEqual(stats.generated_embeddings, 2)
        self.assertEqual(adapter.calls, 2)

    def test_unavailable_cache_fails_open(self):
        invalid_path = Path(self.temp.name) / "cache-is-a-directory"
        invalid_path.mkdir()
        coordinator, _ = embedder(invalid_path)

        with self.assertLogs(
            "services.rag.indexing.cache", level="WARNING"
        ):
            vectors, stats = coordinator.embed(
                self.nodes, expected_dimension=2
            )

        self.assertEqual(set(vectors), {"n1", "n2"})
        self.assertEqual(stats.generated_embeddings, 2)
        self.assertEqual(stats.cache_read_failures, 1)
        self.assertEqual(stats.cache_write_failures, 1)


class ParsingQualityObservationTests(unittest.TestCase):
    def test_low_text_pdf_is_observed_but_not_rejected(self):
        document, diagnostics = DefaultDocumentPreprocessor().process(Document(
            text="扫描页中的少量文字",
            metadata={"extension": ".pdf", "filename": "scan.pdf"},
        ))

        self.assertTrue(document.text)
        self.assertEqual(diagnostics["parse_status"], "parsed")
        self.assertEqual(diagnostics["quality_gate_mode"], "observe_only")
        self.assertTrue(diagnostics["quality_passed"])
        self.assertFalse(diagnostics["quality_would_pass"])
        self.assertIn("low_text_coverage", diagnostics["quality_warnings"])
        self.assertEqual(diagnostics["ocr_pages"], 0)


class FakeConverter:
    def __init__(self, text="", error=None):
        self.text = text
        self.error = error

    def convert_stream(self, stream, **kwargs):
        if self.error is not None:
            raise self.error
        return SimpleNamespace(markdown=self.text)


class FakePdfOcr:
    engine = "tesseract"
    engine_version = "5.5.3"
    model_family = "tessdata_fast"
    model_version = "4.1.0"

    def __init__(self, text="OCR 中文 text", error=None):
        self.text = text
        self.error = error
        self.extract_calls = 0

    def page_count(self, file_bytes):
        return 2

    def extract(self, file_bytes):
        self.extract_calls += 1
        if self.error is not None:
            raise self.error
        return PdfOcrResult(text=self.text, page_count=2, ocr_pages=2)


class PdfOcrRoutingTests(unittest.TestCase):
    def _load(self, converter, ocr):
        return MarkItDownDocumentLoader(
            lambda: converter,
            pdf_ocr=ocr,
            pdf_ocr_min_chars=20,
            pdf_ocr_min_chars_per_page=5,
        ).load(b"%PDF fixture", "scan.pdf")

    def test_normal_text_pdf_does_not_call_ocr(self):
        ocr = FakePdfOcr()
        document = self._load(FakeConverter("正常正文" * 20), ocr)

        self.assertEqual(document.metadata["ocr_status"], "not_needed")
        self.assertEqual(document.metadata["converter"], "markitdown")
        self.assertEqual(ocr.extract_calls, 0)

    def test_low_text_or_failed_conversion_uses_local_ocr(self):
        for converter in (
            FakeConverter("少量"),
            FakeConverter(error=RuntimeError("conversion failed")),
        ):
            with self.subTest(error=converter.error is not None):
                ocr = FakePdfOcr("OCR 后的中文和 English 正文" * 3)
                document = self._load(converter, ocr)

                self.assertEqual(document.metadata["ocr_status"], "applied")
                self.assertEqual(document.metadata["converter"], "tesseract_ocr")
                self.assertEqual(document.metadata["ocr_pages"], 2)
                self.assertEqual(ocr.extract_calls, 1)

    def test_ocr_failure_keeps_nonempty_markitdown_text_observe_only(self):
        ocr = FakePdfOcr(error=RuntimeError("ocr failed"))
        document = self._load(FakeConverter("少量"), ocr)

        self.assertEqual(document.text, "少量")
        self.assertEqual(document.metadata["ocr_status"], "failed")

    def test_empty_text_still_rejects_when_ocr_cannot_recover(self):
        ocr = FakePdfOcr(text="")
        with self.assertRaisesRegex(ValueError, "无有效 Markdown"):
            self._load(FakeConverter(""), ocr)


class LocalOcrRuntimeTests(unittest.TestCase):
    def test_temporary_directory_is_resolved_before_subprocess(self):
        source = Path(__file__).parents[1] / "fastapi-app" / "services" / "rag"
        runtime = LocalTesseractPdfOcr(
            tesseract_path="/missing/tesseract",
            pdftoppm_path="/missing/pdftoppm",
            pdfinfo_path="/missing/pdfinfo",
            tessdata_path=source,
        )
        with mock.patch(
            "services.rag.document.ocr.tempfile.TemporaryDirectory"
        ) as temporary, mock.patch(
            "services.rag.document.ocr.subprocess.run"
        ) as run, mock.patch.object(Path, "write_bytes"):
            temporary.return_value.__enter__.return_value = "/tmp/symlinked"
            run.return_value = SimpleNamespace(stdout="Pages: 1\n")

            self.assertEqual(runtime.page_count(b"pdf"), 1)

        invoked_path = Path(run.call_args.args[0][1])
        self.assertEqual(invoked_path.parent, Path("/tmp/symlinked").resolve())


class OcrMetadataPropagationTests(unittest.TestCase):
    def test_prepare_document_preserves_loader_ocr_diagnostics(self):
        loaded = Document(
            text="OCR 中文正文和 English content",
            metadata={
                "filename": "scan.pdf",
                "extension": ".pdf",
                "converter": "tesseract_ocr",
                "ocr_status": "applied",
                "ocr_pages": 2,
                "page_count": 2,
            },
        )
        loader = SimpleNamespace(load=lambda file_bytes, filename: loaded)
        with tempfile.TemporaryDirectory() as folder:
            service = KnowledgeService(
                document_loader=loader,
                embedding=FakeAdapter(),
                artifact_root=folder,
            )
            prepared = service.prepare_document(b"pdf", "scan.pdf")

        self.assertEqual(prepared["diagnostics"]["ocr_status"], "applied")
        self.assertEqual(prepared["diagnostics"]["ocr_pages"], 2)
        self.assertEqual(prepared["diagnostics"]["page_count"], 2)


class ReleaseSmokeContractTests(unittest.TestCase):
    def test_fixed_smoke_set_binds_20_approved_golden_cases(self):
        root = Path(__file__).parents[1]
        cases, smoke = _validated_cases(
            root / "config" / "rag_golden_dataset_v0.json",
            root / "config" / "rag_phase4_release_smoke_v0.json",
        )

        self.assertEqual(len(cases), 20)
        self.assertEqual(len({case["id"] for case in cases}), 20)
        self.assertTrue(all(case["expected_evidence"] for case in cases))
        self.assertTrue(smoke["gate"]["active_publish_requires_human"])

    def test_release_smoke_attestation_is_immutable(self):
        with tempfile.TemporaryDirectory() as folder:
            store = KnowledgeArtifactRepository(folder).release_smokes
            value = {
                "schema_version": RELEASE_SMOKE_SCHEMA_VERSION,
                "release_id": "a" * 32,
                "passed": True,
            }
            store.put(value)
            store.put(value)
            self.assertEqual(store.get("a" * 32), value)
            with self.assertRaisesRegex(RuntimeError, "冲突"):
                store.put({**value, "passed": False})


class FakeCollection:
    def __init__(self, embeddings):
        self.embeddings = embeddings

    def get(self, include=None):
        return {"ids": ["n1", "n2"], "embeddings": self.embeddings}


class FakeClient:
    def __init__(self, embeddings):
        self.collection = FakeCollection(embeddings)

    def get_collection(self, name):
        return self.collection


class FullVectorValidationTests(unittest.TestCase):
    def _writer(self, embeddings):
        client = FakeClient(embeddings)
        return LlamaIndexChromaIndexWriter(
            client_provider=lambda: client,
            embedding_adapter=FakeAdapter(),
        )

    def test_full_validation_accepts_all_finite_vectors(self):
        result = self._writer([[1.0, 0.0], [0.0, 1.0]]).validate_collection(
            collection_name="candidate",
            expected_node_ids=["n1", "n2"],
            expected_dimension=2,
        )
        self.assertEqual(result["validated_vectors"], 2)

    def test_full_validation_rejects_nan_zero_and_dimension_mismatch(self):
        for embeddings, message in (
            ([[float("nan"), 1.0], [0.0, 1.0]], "NaN"),
            ([[0.0, 0.0], [0.0, 1.0]], "零范数"),
            ([[1.0], [0.0, 1.0]], "维度"),
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(RuntimeError, message):
                    self._writer(embeddings).validate_collection(
                        collection_name="candidate",
                        expected_node_ids=["n1", "n2"],
                        expected_dimension=2,
                    )


if __name__ == "__main__":
    unittest.main()
