import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]
BACKEND_DIR = PROJECT_ROOT / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from services.rag.core import Document, SourceInfo  # noqa: E402
from services.rag.document import (  # noqa: E402
    DefaultDocumentPreprocessor,
    DoclingPaperLoader,
    GrobidMetadataEnricher,
    KnowledgeArtifactRepository,
    PAPER_DOCUMENT_SCHEMA_VERSION,
    PaperCorpusCandidateBuilder,
    PaperDocumentNormalizer,
    ParserRouter,
    deterministic_paper_document_id,
    parse_grobid_tei,
)


class _FallbackLoader:
    def __init__(
        self,
        text="# Paper\n\n## Abstract\n\n" + "Evidence body. " * 30,
    ):
        self.text = text

    def load(self, file_bytes, filename):
        return Document(
            self.text,
            {"filename": filename, "extension": Path(filename).suffix,
             "converter": "markitdown"},
        )


class _NativeDoclingDocument:
    def export_to_markdown(self, **kwargs):
        return "# Docling Paper\n\n## Abstract\n\nStructured abstract.\n\n| A | B |\n|---|---|\n|1|2|"

    def export_to_dict(self):
        return {
            "title": "Docling Paper",
            "pages": {"1": {}},
            "body": [{"label": "table"}, {"label": "formula"}],
        }


class _DoclingConverter:
    def convert(self, source):
        return type("Result", (), {"document": _NativeDoclingDocument()})()


class _DoclingLoaderUnavailable:
    available = False


class _Response:
    status_code = 200

    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class _GrobidClient:
    def __init__(self, xml):
        self.xml = xml
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response(self.xml)


TEI = """<?xml version="1.0"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader>
<fileDesc><titleStmt><title>GROBID Paper</title><author><persName>
<forename type="first">Ada</forename><surname>Lovelace</surname>
</persName><affiliation><orgName>Analytical Engine Lab</orgName></affiliation>
</author></titleStmt><publicationStmt><date when="2025-04-01"/>
<idno type="DOI">10.1000/test</idno></publicationStmt></fileDesc>
<profileDesc><abstract><p>Abstract text.</p></abstract>
<textClass><keywords><term>anomaly detection</term></keywords></textClass>
</profileDesc></teiHeader><text><body><div><head>Introduction</head></div></body>
<back><listBibl><biblStruct><analytic><title>Reference A</title></analytic>
</biblStruct></listBibl></back></text></TEI>"""


def _source(payload=b"pdf"):
    import hashlib
    return SourceInfo(
        filename="paper.pdf", extension=".pdf", media_type="application/pdf",
        byte_size=len(payload), sha256=hashlib.sha256(payload).hexdigest(),
        storage_key="raw/test.bin", uploaded_at="2026-08-26T00:00:00+00:00",
    )


class PaperDocumentV2Tests(unittest.TestCase):
    def test_docling_adapter_uses_stream_and_exports_structure(self):
        loader = DoclingPaperLoader(
            converter_provider=lambda: _DoclingConverter(),
            stream_factory=lambda name, raw: {"name": name, "raw": raw},
        )
        result = loader.load(b"pdf", "paper.pdf")
        self.assertEqual(result.document.metadata["converter"], "docling")
        self.assertIn("Structured abstract", result.document.text)
        self.assertEqual(result.diagnostics["page_count"], 1)
        self.assertEqual(result.diagnostics["table_count"], 1)
        self.assertEqual(result.diagnostics["formula_count"], 1)

    def test_grobid_tei_extracts_academic_metadata_without_consolidation(self):
        parsed = parse_grobid_tei(TEI)
        self.assertEqual(parsed["title"], "GROBID Paper")
        self.assertEqual(parsed["authors"], ["Ada Lovelace"])
        self.assertEqual(parsed["publication_year"], 2025)
        self.assertEqual(parsed["doi"], "10.1000/test")
        self.assertEqual(len(parsed["references"]), 1)

        client = _GrobidClient(TEI)
        result = GrobidMetadataEnricher(
            base_url="http://grobid:8070", client_provider=lambda: client
        ).enrich(b"pdf", "paper.pdf")
        self.assertEqual(result.diagnostics["grobid_status"], "completed")
        _, kwargs = client.calls[0]
        self.assertEqual(kwargs["data"]["consolidateHeader"], "0")
        self.assertEqual(kwargs["data"]["consolidateCitations"], "0")

    def test_normalizer_has_stable_ids_relations_and_conflict_diagnostics(self):
        source = _source()
        normalizer = PaperDocumentNormalizer()
        kwargs = {
            "source": source,
            "work_id": "work-1",
            "source_parser": "docling",
            "parser_metadata": {"title": "Parsed Title"},
            "grobid_metadata": {"title": "Conflicting Title", "doi": "10/x"},
            "catalog_metadata": {"publication_year": 2025},
            "diagnostics": {"quality_status": "passed", "publish_eligible": True},
        }
        document = Document(
            "# Parsed Title\n\n## Abstract\n\nSummary.\n\n"
            "## Results\n\n| Metric | Value |\n|---|---|\n|AUROC|99|"
        )
        first = normalizer.normalize(document, **kwargs)
        second = normalizer.normalize(document, **kwargs)
        self.assertEqual(first.document_id, deterministic_paper_document_id(source))
        self.assertEqual(
            [block.block_id for block in first.blocks],
            [block.block_id for block in second.blocks],
        )
        self.assertTrue(first.relations)
        self.assertEqual(first.bibliographic_metadata["doi"], "10/x")
        self.assertTrue(first.diagnostics["metadata_conflicts"])

    def test_router_explicitly_marks_docling_and_grobid_degradation(self):
        router = ParserRouter(
            fallback_loader=_FallbackLoader(),
            preprocessor=DefaultDocumentPreprocessor(),
            docling_loader=_DoclingLoaderUnavailable(),
            grobid_enricher=GrobidMetadataEnricher(enabled=True, base_url=""),
        )
        result = router.parse(
            b"not-a-real-pdf", "paper.pdf", source=_source(b"not-a-real-pdf"),
            catalog_metadata={
                "quality_hints": ["manual_visual_review_required"]
            },
        )
        diagnostics = result.diagnostics
        self.assertEqual(diagnostics["primary_parser"], "markitdown")
        self.assertTrue(diagnostics["fallback_used"])
        self.assertEqual(diagnostics["grobid_status"], "not_configured")
        self.assertEqual(diagnostics["quality_status"], "degraded")
        self.assertTrue(diagnostics["publish_eligible"])
        self.assertTrue(diagnostics["manual_review_required"])

    def test_paper_store_detects_tamper_and_does_not_replace_v1_store(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = KnowledgeArtifactRepository(directory)
            paper = PaperDocumentNormalizer().normalize(
                Document("# Paper\n\nEvidence."), source=_source(),
                source_parser="markitdown",
                diagnostics={"quality_status": "degraded", "publish_eligible": True},
            )
            repository.paper_documents.put(paper)
            self.assertEqual(
                repository.paper_documents.get(paper.document_id)["schema_version"],
                PAPER_DOCUMENT_SCHEMA_VERSION,
            )
            self.assertFalse(
                repository.documents.path_for(paper.document_id).exists()
            )
            path = repository.paper_documents.path_for(paper.document_id)
            record = json.loads(path.read_text(encoding="utf-8"))
            record["normalized_markdown"] += "tampered"
            path.write_text(json.dumps(record), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA256"):
                repository.paper_documents.get(paper.document_id)

    def test_candidate_builder_is_idempotent_and_never_publishes(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = KnowledgeArtifactRepository(directory)
            router = ParserRouter(
                fallback_loader=_FallbackLoader(),
                preprocessor=DefaultDocumentPreprocessor(),
                docling_loader=_DoclingLoaderUnavailable(),
                grobid_enricher=GrobidMetadataEnricher(enabled=False),
            )
            entry = {
                "filename": "paper.pdf", "file_bytes": b"pdf",
                "work_id": "work-1", "catalog_metadata": {"title": "Paper"},
            }
            first = PaperCorpusCandidateBuilder(repository, router).build([entry])
            second = PaperCorpusCandidateBuilder(repository, router).build([entry])
            self.assertEqual(first["candidate_id"], second["candidate_id"])
            self.assertFalse(first["documents"][0]["reused"])
            self.assertTrue(second["documents"][0]["reused"])
            self.assertIsNone(repository.releases.active())
            self.assertEqual(first["status"], "staged_not_published")


if __name__ == "__main__":
    unittest.main()
