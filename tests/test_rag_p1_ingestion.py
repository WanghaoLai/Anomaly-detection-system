import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
import sys

sys.path.insert(0, str(BACKEND_DIR))

import services.knowledge_service as knowledge_module  # noqa: E402
from services.knowledge_service import (  # noqa: E402
    DOC_COLLECTION,
    KnowledgeService,
)
from services.rag.document.storage import (  # noqa: E402
    RELEASE_SMOKE_SCHEMA_VERSION,
    deterministic_document_id,
    deterministic_node_id,
    sha256_bytes,
)
from services.rag.core.contracts import Document, Node  # noqa: E402
from services.rag.document.splitting import PARSER_SCHEMA_VERSION  # noqa: E402


class MutableEmbedding:
    def __init__(self, dimension=2):
        self.dimension = dimension
        self.fail = False
        self.document_calls = 0

    def embed_documents(self, texts):
        self.document_calls += 1
        if self.fail:
            raise RuntimeError("故障注入: embedding unavailable")
        return [
            [float(index + 1) / self.dimension for index in range(self.dimension)]
            for _ in texts
        ]

    def embed_queries(self, texts):
        return self.embed_documents(texts)

    def embed_query(self, query):
        return self.embed_documents([query])[0]


class MutableMarkdownConverter:
    def __init__(self, markdown="# 手册\n\n原始内容"):
        self.markdown = markdown

    def convert_stream(self, stream, **kwargs):
        stream.read()
        return SimpleNamespace(markdown=self.markdown)


class RagP1IngestionTests(unittest.TestCase):
    def setUp(self):
        self.chroma_dir = tempfile.TemporaryDirectory()
        self.artifact_dir = tempfile.TemporaryDirectory()
        self.config_patch = patch.dict(
            knowledge_module.AI_CONFIG,
            {
                "vector_store_provider": "chroma",
                "qdrant_mode": "local",
                "rag_release_smoke_required": False,
            },
            clear=False,
        )
        self.patch = patch.object(
            knowledge_module, "CHROMA_PATH", self.chroma_dir.name
        )
        self.config_patch.start()
        self.patch.start()
        self.embedding = MutableEmbedding(2)
        self.converter = MutableMarkdownConverter()
        self.service = KnowledgeService(
            embedding_model="test-embedding-v1",
            markdown_converter=self.converter,
            embedding=self.embedding,
            artifact_root=self.artifact_dir.name,
        )

    def tearDown(self):
        self.patch.stop()
        self.config_patch.stop()
        self.artifact_dir.cleanup()
        self.chroma_dir.cleanup()

    def _publish_first(self):
        staged = self.service.stage_document_release(b"raw-v1", "manual.md")
        previous = self.service.publish_staged_release(staged["release_id"])
        self.assertIsNone(previous)
        return staged

    def test_required_release_smoke_blocks_publish_until_20_of_20_attested(self):
        staged = self.service.stage_document_release(b"raw-v1", "manual.md")
        smoke_set_path = (
            Path(__file__).parents[1]
            / "config"
            / "rag_phase4_release_smoke_v0.json"
        )
        configured = {
            "rag_release_smoke_required": True,
            "rag_release_smoke_set_path": str(smoke_set_path),
        }
        with patch.dict(knowledge_module.AI_CONFIG, configured, clear=False):
            with self.assertRaisesRegex(FileNotFoundError, "证明不存在"):
                self.service.publish_staged_release(staged["release_id"])

            manifest_path = self.service.artifact_repository.releases.release_path(
                staged["release_id"]
            )
            self.service.artifact_repository.release_smokes.put({
                "schema_version": RELEASE_SMOKE_SCHEMA_VERSION,
                "release_id": staged["release_id"],
                "manifest_sha256": sha256_bytes(manifest_path.read_bytes()),
                "smoke_set_sha256": sha256_bytes(smoke_set_path.read_bytes()),
                "question_count": 20,
                "evidence_hits": 20,
                "passed": True,
            })
            previous = self.service.publish_staged_release(staged["release_id"])

        self.assertIsNone(previous)

    def test_publish_prewarms_bm25_and_warm_failure_does_not_rollback(self):
        staged = self.service.stage_document_release(b"raw-v1", "manual.md")
        self.service.warm_lexical_cache = Mock(
            side_effect=RuntimeError("warm failed")
        )

        with self.assertLogs(
            "services.knowledge_service", level="ERROR"
        ) as captured:
            previous = self.service.publish_staged_release(staged["release_id"])

        self.assertIsNone(previous)
        self.assertEqual(
            self.service.artifact_repository.releases.active()["release_id"],
            staged["release_id"],
        )
        self.assertIn("BM25 预热失败", "\n".join(captured.output))

    def test_original_file_document_nodes_and_source_are_preserved(self):
        staged = self.service.stage_document_release(b"raw-v1", "folder\\manual.md")

        # 影子构建完成前/后都不会修改 P0 发布 collection。
        self.assertEqual(self.service.active_collection_name(), DOC_COLLECTION)
        self.assertEqual(self.service.collection.count(), 0)

        record = self.service.artifact_repository.documents.get(staged["doc_id"])
        source = record["source"]
        self.assertEqual(source["filename"], "manual.md")
        self.assertEqual(source["byte_size"], len(b"raw-v1"))
        self.assertTrue(source["storage_key"].startswith("raw/"))
        self.assertEqual(self.service.artifact_repository.files.verify(source), [])
        self.assertEqual(len(record["nodes"]), staged["chunk_count"])
        self.assertEqual(
            len({node["node_id"] for node in record["nodes"]}),
            len(record["nodes"]),
        )

    def test_shadow_can_be_fully_rebuilt_with_stable_non_duplicate_nodes(self):
        first = self._publish_first()
        calls_after_first = self.embedding.document_calls
        active_manifest = self.service.artifact_repository.releases.get(
            first["release_id"]
        )

        rebuilt = self.service.rebuild_shadow_from_active()
        rebuilt_collection = self.service.client.get_collection(
            name=rebuilt["collection_name"]
        )
        rebuilt_ids = rebuilt_collection.get()["ids"]

        self.assertNotEqual(rebuilt["collection_name"], first["collection_name"])
        self.assertEqual(rebuilt["node_ids_sha256"], active_manifest["node_ids_sha256"])
        self.assertEqual(len(rebuilt_ids), len(set(rebuilt_ids)))
        self.assertEqual(rebuilt_collection.count(), rebuilt["node_count"])
        self.assertEqual(self.service.active_collection_name(), first["collection_name"])
        self.assertEqual(self.embedding.document_calls, calls_after_first)
        self.assertEqual(
            rebuilt["indexing"]["reused_embeddings"], rebuilt["node_count"]
        )
        self.assertEqual(rebuilt["indexing"]["generated_embeddings"], 0)
        self.assertEqual(rebuilt["indexing"]["embedding_cache_hit_rate"], 1.0)
        self.assertEqual(rebuilt["indexing"]["embedding_api_calls"], 0)

    def test_same_upload_is_idempotent_and_does_not_rebuild_or_duplicate(self):
        first = self._publish_first()
        calls_after_first = self.embedding.document_calls

        second = self.service.stage_document_release(b"raw-v1", "manual.md")

        self.assertTrue(second["unchanged"])
        self.assertEqual(second["doc_id"], first["doc_id"])
        self.assertEqual(second["release_id"], first["release_id"])
        self.assertEqual(self.embedding.document_calls, calls_after_first)
        self.assertEqual(self.service.collection.count(), first["chunk_count"])

    def test_access_policy_is_in_manifest_and_every_indexed_node(self):
        staged = self.service.stage_document_release(
            b"raw-v1",
            "manual.md",
            visibility="admin_only",
            allowed_roles="管理员",
        )
        manifest = self.service.artifact_repository.releases.get(
            staged["release_id"]
        )
        expected = {
            "visibility": "admin_only",
            "allowed_roles": "管理员",
            "allowed_user_ids": "",
        }

        self.assertEqual(manifest["access_policies"][staged["doc_id"]], expected)
        collection = self.service.client.get_collection(
            name=staged["collection_name"]
        )
        for metadata in collection.get(include=["metadatas"])["metadatas"]:
            self.assertEqual(
                {key: metadata.get(key, "") for key in expected}, expected
            )

    def test_permission_change_rebuilds_shadow_without_changing_document_id(self):
        first = self._publish_first()

        restricted = self.service.stage_document_release(
            b"raw-v1",
            "manual.md",
            visibility="admin_only",
            allowed_roles="管理员",
        )

        self.assertEqual(restricted["doc_id"], first["doc_id"])
        self.assertFalse(restricted["unchanged"])
        self.assertTrue(restricted["permission_changed"])
        self.assertNotEqual(restricted["release_id"], first["release_id"])
        # 尚未发布时，在线索引和权限仍保持原版本。
        self.assertEqual(self.service.active_collection_name(), first["collection_name"])

    def test_dimension_change_is_blocked_without_affecting_published_release(self):
        first = self._publish_first()
        pointer_before = self.service.artifact_repository.releases.active()
        active_count = self.service.collection.count()
        self.converter.markdown = "# 手册\n\n新内容"
        self.embedding.dimension = 3

        with self.assertRaisesRegex(RuntimeError, "维度"):
            self.service.stage_document_release(b"raw-v2", "manual.md")

        self.assertEqual(self.service.artifact_repository.releases.active(), pointer_before)
        self.assertEqual(self.service.active_collection_name(), first["collection_name"])
        self.assertEqual(self.service.collection.count(), active_count)

    def test_model_change_is_blocked_before_writing_candidate(self):
        first = self._publish_first()
        pointer_before = self.service.artifact_repository.releases.active()
        other = KnowledgeService(
            embedding_model="test-embedding-v2",
            markdown_converter=self.converter,
            embedding=MutableEmbedding(2),
            artifact_root=self.artifact_dir.name,
        )

        with self.assertRaisesRegex(RuntimeError, "embedding 配置不一致"):
            other.stage_document_release(b"raw-v2", "other.md")

        self.assertEqual(other.artifact_repository.releases.active(), pointer_before)
        self.assertEqual(other.active_collection_name(), first["collection_name"])

    def test_failure_and_pointer_rollback_leave_published_release_unchanged(self):
        first = self._publish_first()
        pointer_before = self.service.artifact_repository.releases.active()
        active_count = self.service.collection.count()
        self.converter.markdown = "# 手册\n\n待发布内容"
        self.embedding.fail = True

        with self.assertRaisesRegex(RuntimeError, "故障注入"):
            self.service.stage_document_release(b"raw-v2", "manual.md")
        self.assertEqual(self.service.artifact_repository.releases.active(), pointer_before)
        self.assertEqual(self.service.collection.count(), active_count)

        self.embedding.fail = False
        staged = self.service.stage_document_release(b"raw-v2", "manual.md")
        previous = self.service.publish_staged_release(staged["release_id"])
        self.assertNotEqual(self.service.active_collection_name(), first["collection_name"])
        self.assertTrue(
            self.service.rollback_published_release(staged["release_id"], previous)
        )
        self.assertEqual(self.service.artifact_repository.releases.active(), pointer_before)
        self.assertEqual(self.service.active_collection_name(), first["collection_name"])

    def test_mysql_file_docstore_and_chroma_reconcile(self):
        staged = self._publish_first()
        sql = [{
            "id": 1,
            "filename": staged["doc_id"],
            "original_name": "manual.md",
            "file_size": staged["file_size"],
            "chunk_count": staged["chunk_count"],
        }]

        report = self.service.reconcile_metadata(sql)

        self.assertTrue(report["healthy"], report["issues"])
        self.assertEqual(report["summary"]["mysql_documents"], 1)
        self.assertEqual(report["summary"]["file_references"], 1)
        self.assertEqual(report["summary"]["docstore_documents"], 1)
        self.assertEqual(report["summary"]["chroma_documents"], 1)
        self.assertEqual(report["summary"]["docstore_nodes"], staged["chunk_count"])
        self.assertEqual(report["summary"]["chroma_chunks"], staged["chunk_count"])

    def test_concurrent_publish_guard_rejects_stale_shadow(self):
        first = self._publish_first()
        self.converter.markdown = "# 手册\n\n版本二"
        second = self.service.stage_document_release(b"raw-v2", "manual.md")
        self.converter.markdown = "# 其他\n\n文档"
        third = self.service.stage_document_release(b"raw-other", "other.md")

        self.service.publish_staged_release(second["release_id"])
        with self.assertRaisesRegex(RuntimeError, "发布基线已变更"):
            self.service.publish_staged_release(third["release_id"])

        self.assertEqual(
            self.service.artifact_repository.releases.active()["release_id"],
            second["release_id"],
        )
        self.assertNotEqual(first["release_id"], second["release_id"])

    def test_legacy_fingerprint_is_stable_across_service_instances(self):
        self.service.collection.add(
            ids=["legacy-node"],
            embeddings=[[1.0, 0.0]],
            documents=["历史内容"],
            metadatas=[{
                "doc_id": "abcdef",
                "filename": "legacy.md",
                "chunk_index": 0,
                "embedding_model": "test-embedding-v1",
            }],
        )
        first = self.service._collection_fingerprint(DOC_COLLECTION)
        other = KnowledgeService(
            embedding_model="test-embedding-v1",
            markdown_converter=self.converter,
            embedding=MutableEmbedding(2),
            artifact_root=self.artifact_dir.name,
        )

        self.assertEqual(first, other._collection_fingerprint(DOC_COLLECTION))

    def test_p2_parser_migration_is_stable_and_does_not_touch_active_release(self):
        source = self.service.artifact_repository.files.put(
            b"legacy-raw", "legacy.md", ".md"
        )
        old_document_id = deterministic_document_id(
            "legacy.md", source.sha256, "legacy-parser-v1"
        )
        text = "# 服务器手册\n\n" + "配置和检查异常检测服务。" * 80
        legacy_metadata = {
            "filename": "legacy.md",
            "extension": ".md",
            "ingestion_schema_version": "legacy-parser-v1",
        }
        legacy_node_metadata = {
            "chunk_index": 0,
            "start": 0,
            "end": len(text),
            "heading_path": "服务器手册",
        }
        legacy_document = Document(
            text=text,
            metadata=legacy_metadata,
            document_id=old_document_id,
            source=source,
        )
        legacy_node = Node(
            text=text,
            metadata=legacy_node_metadata,
            node_id=deterministic_node_id(
                old_document_id, 0, text, legacy_node_metadata
            ),
        )
        self.service.artifact_repository.documents.put(
            legacy_document, [legacy_node], {"chunk_count": 1}
        )
        _, guard = self.service._active_catalog_and_guard()
        legacy_release = self.service._build_shadow_release(
            {"legacy.md": old_document_id}, guard
        )
        self.service.publish_staged_release(legacy_release["release_id"])
        pointer_before = self.service.artifact_repository.releases.active()

        first = self.service.stage_node_parser_migration()
        second = self.service.stage_node_parser_migration()

        self.assertEqual(
            self.service.artifact_repository.releases.active(), pointer_before
        )
        self.assertEqual(first["changed_documents"], 1)
        self.assertEqual(
            first["release"]["node_ids_sha256"],
            second["release"]["node_ids_sha256"],
        )
        self.assertEqual(
            first["release"]["node_count"], second["release"]["node_count"]
        )
        item = first["documents"][0]
        self.assertNotEqual(item["old_document_id"], item["new_document_id"])
        migrated = self.service.artifact_repository.documents.get(
            item["new_document_id"]
        )
        self.assertTrue(migrated["nodes"])
        self.assertTrue(all(
            node["metadata"]["parser_schema_version"] == PARSER_SCHEMA_VERSION
            for node in migrated["nodes"]
        ))


if __name__ == "__main__":
    unittest.main()
