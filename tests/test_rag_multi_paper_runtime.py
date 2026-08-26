import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).parents[1]
BACKEND_DIR = PROJECT_ROOT / "fastapi-app"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BACKEND_DIR))

from scripts.evaluate_rag_multi_paper import (  # noqa: E402
    _acl_case_result,
    _ndcg,
    _rrf,
)
from scripts.ingest_rag_multi_paper_baseline import build_entries  # noqa: E402
from services.knowledge_service import KnowledgeService  # noqa: E402
from services.rag.core import SourceInfo  # noqa: E402


class _Files:
    def put(self, content, filename, extension):
        return SourceInfo(
            filename=filename,
            extension=extension,
            media_type="application/pdf",
            byte_size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            storage_key="raw/test.bin",
            uploaded_at="2026-08-25T00:00:00+00:00",
        )


class _Documents:
    def __init__(self):
        self.records = {}

    def get(self, document_id):
        if document_id not in self.records:
            raise FileNotFoundError(document_id)
        return self.records[document_id]

    def put(self, document, nodes, diagnostics):
        record = {
            "document_id": document.document_id,
            "metadata": dict(document.metadata),
            "source": {"sha256": document.source.sha256},
            "diagnostics": dict(diagnostics),
            "nodes": [
                {"node_id": node.node_id, "text": node.text,
                 "metadata": dict(node.metadata)}
                for node in nodes
            ],
        }
        self.records[document.document_id] = record
        return record


class MultiPaperRuntimeTests(unittest.TestCase):
    def test_manifest_entry_builder_rejects_content_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            documents = []
            for index in range(15):
                name = f"paper-{index}.pdf"
                payload = f"pdf-{index}".encode()
                (root / name).write_bytes(payload)
                documents.append({
                    "filename": name,
                    "byte_size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "work_id": f"work-{index}",
                    "document_id": f"frozen-{index}",
                })
            manifest = {"documents": documents}
            entries = build_entries(manifest, root, visibility="public")
            self.assertEqual(len(entries), 15)
            (root / "paper-3.pdf").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "大小漂移"):
                build_entries(manifest, root, visibility="public")

    def test_stage_corpus_builds_one_shadow_release(self):
        service = KnowledgeService.__new__(KnowledgeService)
        service._collection = SimpleNamespace(count=lambda: 0)
        service._collection_name = None
        docs = _Documents()
        service._artifact_repository = SimpleNamespace(
            files=_Files(), documents=docs
        )
        service._active_catalog_and_guard = lambda: ({"manual.pdf": "old"}, {
            "active_pointer": {"release_id": "old"},
            "access_policies": {"old": {
                "visibility": "internal",
                "allowed_roles": "管理员,用户",
                "allowed_user_ids": "",
            }},
        })
        service.prepare_document = lambda *args, **kwargs: {
            "markdown": "# Paper\nEvidence",
            "chunks": [{
                "content": "Evidence", "node_id": "a" * 32,
                "chunk_index": 0,
            }],
            "diagnostics": {"chunk_count": 1},
        }
        calls = []

        def build(candidate, guard, access_policies=None):
            calls.append(dict(candidate))
            return {
                "release_id": "b" * 32,
                "collection_name": "knowledge_shadow_" + "b" * 32,
                "document_count": len(candidate),
                "node_count": 2,
            }

        service._build_shadow_release = build
        payload = b"paper"
        result = service.stage_corpus_release([{
            "filename": "paper.pdf",
            "file_bytes": payload,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "work_id": "work-1",
            "frozen_document_id": "paper-frozen",
            "visibility": "public",
        }])
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["preserved_existing_documents"], 1)
        self.assertEqual(result["corpus_document_count"], 1)
        self.assertEqual(result["documents"][0]["work_id"], "work-1")

    def test_rrf_and_ndcg_reward_relevant_documents(self):
        dense = [
            {"node_id": "n1", "doc_id": "d1"},
            {"node_id": "n2", "doc_id": "d2"},
        ]
        lexical = [
            {"node_id": "n2", "doc_id": "d2"},
            {"node_id": "n1", "doc_id": "d1"},
        ]
        fused = _rrf(dense, lexical)
        self.assertEqual({item["node_id"] for item in fused}, {"n1", "n2"})
        self.assertGreater(_ndcg(fused, {"d1", "d2"}, 10), 0.9)

        duplicate_nodes = [
            {"node_id": "n1", "doc_id": "d1"},
            {"node_id": "n3", "doc_id": "d1"},
            {"node_id": "n2", "doc_id": "d2"},
        ]
        self.assertLessEqual(_ndcg(duplicate_nodes, {"d1", "d2"}, 10), 1.0)

    def test_acl_gold_cases_cover_denial_and_allow(self):
        dataset = {
            "principals": {
                "public_user": {"clearance": "public"},
                "internal_user": {"clearance": "internal"},
            }
        }
        denied = {
            "access_principal": "public_user",
            "relevant_work_ids": [],
            "corpus_acl_overrides": {"realnet": "internal"},
        }
        allowed = {
            "access_principal": "internal_user",
            "relevant_work_ids": ["realnet"],
            "corpus_acl_overrides": {"realnet": "internal"},
        }
        self.assertTrue(_acl_case_result(denied, dataset)["passed"])
        self.assertTrue(_acl_case_result(allowed, dataset)["passed"])


if __name__ == "__main__":
    unittest.main()
