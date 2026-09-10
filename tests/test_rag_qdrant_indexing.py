import asyncio
import hashlib
import json
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FASTAPI_ROOT = PROJECT_ROOT / "fastapi-app"
if str(FASTAPI_ROOT) not in sys.path:
    sys.path.insert(0, str(FASTAPI_ROOT))

from services.rag.core import Node  # noqa: E402
from services.rag.document.storage import ReleaseManifestStore  # noqa: E402
from services.rag.indexing import (  # noqa: E402
    POINT_ID_SCHEMA_VERSION,
    QDRANT_WRITER_SCHEMA_VERSION,
    EmbeddingBuildStats,
    QdrantIndexWriter,
    QdrantRuntimeConfig,
    QdrantVectorStore,
    create_qdrant_client,
    point_id_for_node,
)
import services.knowledge_service as knowledge_module  # noqa: E402
from services.knowledge_service import KnowledgeService  # noqa: E402
from settings import AI_CONFIG  # noqa: E402


class _EmbeddingAdapter:
    embed_batch_size = 25


class _StaticNodeEmbedder:
    @staticmethod
    def _vectors(nodes):
        return {
            node.node_id: [1.0, float(index + 1)]
            for index, node in enumerate(nodes)
        }

    def embed(self, nodes, expected_dimension=None):
        return self._vectors(nodes), EmbeddingBuildStats(
            cache_hits=len(nodes),
            embedding_batches=0,
        )

    async def aembed(self, nodes, expected_dimension=None):
        return self.embed(nodes, expected_dimension)


class _ServiceEmbedding:
    provider = "test-provider"
    schema_version = "test-embedding-v1"
    normalized = True

    @staticmethod
    def embed_documents(texts):
        return [[1.0, 0.0, 0.0] for _ in texts]

    @staticmethod
    def embed_query(query):
        return [1.0, 0.0, 0.0]

    @staticmethod
    def embed_queries(queries):
        return [[1.0, 0.0, 0.0] for _ in queries]

    async def aembed_documents(self, texts):
        return self.embed_documents(texts)


class _MarkdownConverter:
    @staticmethod
    def convert_stream(stream, **kwargs):
        stream.read()
        return SimpleNamespace(markdown="# 手册\n\n工业异常检测配置。")


class QdrantPointIdentityTests(unittest.TestCase):
    def test_uuid_mapping_is_stable_and_versioned(self):
        self.assertEqual(POINT_ID_SCHEMA_VERSION, "uuid5-node-id-v1")
        self.assertEqual(
            point_id_for_node("abc"),
            "b326be19-350d-5940-9b83-b26bc5633af0",
        )
        self.assertEqual(point_id_for_node("abc"), point_id_for_node("abc"))
        self.assertNotEqual(point_id_for_node("abc"), point_id_for_node("abd"))
        with self.assertRaises(ValueError):
            point_id_for_node("")

    def test_runtime_config_fails_closed(self):
        with self.assertRaises(ValueError):
            QdrantRuntimeConfig(mode="unknown", path="/tmp/x").validate()
        with self.assertRaises(ValueError):
            QdrantRuntimeConfig(mode="local", path="").validate()
        with self.assertRaises(ValueError):
            QdrantRuntimeConfig(mode="server", url="").validate()
        self.assertEqual(
            QdrantRuntimeConfig(
                mode="server", url="http://127.0.0.1:6333"
            ).validate().mode,
            "server",
        )

    def test_https_url_without_port_uses_standard_443(self):
        with patch(
            "services.rag.indexing.qdrant_store.QdrantClient"
        ) as client_factory:
            create_qdrant_client(QdrantRuntimeConfig(
                mode="server",
                url="https://example.cloud.qdrant.io",
                api_key="secret",
            ))
        self.assertEqual(client_factory.call_args.kwargs["port"], 443)
        self.assertEqual(
            client_factory.call_args.kwargs["url"],
            "https://example.cloud.qdrant.io",
        )


class ReleaseManifestCompatibilityTests(unittest.TestCase):
    def test_v1_chroma_pointer_remains_readable_after_v2_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release_id = "a" * 32
            manifest = {
                "schema_version": "shadow-release-v1",
                "release_id": release_id,
                "collection_name": f"knowledge_shadow_{release_id}",
                "indexing": {},
            }
            payload = json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            release_path = root / "releases" / f"{release_id}.json"
            release_path.parent.mkdir(parents=True)
            release_path.write_bytes(payload)
            pointer = {
                "schema_version": "shadow-release-v1",
                "release_id": release_id,
                "collection_name": manifest["collection_name"],
                "manifest_sha256": hashlib.sha256(payload).hexdigest(),
            }
            (root / "active_release.json").write_text(
                json.dumps(pointer), encoding="utf-8"
            )
            store = ReleaseManifestStore(root)
            self.assertEqual(store.get(release_id), manifest)
            self.assertEqual(store.active()["release_id"], release_id)


class QdrantVectorStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.client = create_qdrant_client(QdrantRuntimeConfig(
            mode="local", path=self.tempdir.name
        ))
        from qdrant_client import models

        self.client.create_collection(
            "knowledge_shadow_vector_store",
            vectors_config=models.VectorParams(
                size=2, distance=models.Distance.COSINE
            ),
        )
        self.store = QdrantVectorStore(
            lambda: self.client,
            lambda: "knowledge_shadow_vector_store",
            scroll_batch_size=2,
        )

    def tearDown(self):
        self.client.close()
        self.tempdir.cleanup()

    def test_add_query_acl_filter_and_scroll(self):
        self.store.add(
            ids=["node-a", "node-b", "node-c"],
            embeddings=[[1, 0], [0, 1], [0.8, 0.2]],
            documents=["A", "B", "C"],
            metadatas=[
                {"doc_id": "doc-1"},
                {"doc_id": "doc-2"},
                {"doc_id": "doc-1"},
            ],
        )
        self.assertEqual(self.store.count(), 3)
        nodes = self.store.list_nodes()
        self.assertEqual({item["node_id"] for item in nodes}, {
            "node-a", "node-b", "node-c"
        })

        results = self.store.query(
            [1, 0], 10, where={"doc_id": {"$in": ["doc-1"]}}
        )
        self.assertEqual({item["node_id"] for item in results}, {
            "node-a", "node-c"
        })
        self.assertAlmostEqual(results[0]["distance"], 1 - results[0]["score"])
        self.assertEqual(
            self.store.query([1, 0], 10, where={"doc_id": {"$in": []}}),
            [],
        )


class QdrantIndexWriterTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.client = create_qdrant_client(QdrantRuntimeConfig(
            mode="local", path=self.tempdir.name
        ))
        self.writer = QdrantIndexWriter(
            client_provider=lambda: self.client,
            embedding_adapter=_EmbeddingAdapter(),
            node_embedder=_StaticNodeEmbedder(),
            insert_batch_size=1,
            require_payload_indexes=False,
        )
        self.nodes = [
            Node(
                node_id="node-a",
                text="alpha",
                metadata={
                    "doc_id": "doc-1",
                    "embedding_text_type": "document",
                    "visibility": "internal",
                    "allowed_roles": "管理员,用户",
                    "allowed_user_ids": "",
                    "release_id": "release-a",
                },
            ),
            Node(
                node_id="node-b",
                text="beta",
                metadata={
                    "doc_id": "doc-2",
                    "embedding_text_type": "document",
                    "visibility": "internal",
                    "allowed_roles": "管理员,用户",
                    "allowed_user_ids": "",
                    "release_id": "release-a",
                },
            ),
        ]

    def tearDown(self):
        self.client.close()
        self.tempdir.cleanup()

    def test_build_validates_original_ids_vectors_and_metadata(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            result = self.writer.build(
                collection_name="knowledge_shadow_qdrant_sync",
                collection_metadata={"embedding_model": "test-model"},
                nodes=self.nodes,
                expected_dimension=2,
            )
        self.assertEqual(result.writer_schema_version, QDRANT_WRITER_SCHEMA_VERSION)
        self.assertEqual(result.node_ids, ("node-a", "node-b"))
        self.assertEqual(result.write_batches, 2)
        report = self.writer.validate_collection(
            collection_name=result.collection_name,
            expected_node_ids=result.node_ids,
            expected_dimension=2,
        )
        self.assertEqual(report["validated_point_ids"], 2)
        records, _ = self.client.scroll(
            result.collection_name, with_payload=True, with_vectors=False
        )
        self.assertEqual(
            {record.payload["node_id"] for record in records},
            {"node-a", "node-b"},
        )

    def test_async_build_uses_same_contract(self):
        async def run():
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                return await self.writer.abuild(
                    collection_name="knowledge_shadow_qdrant_async",
                    collection_metadata={"embedding_model": "test-model"},
                    nodes=self.nodes,
                    expected_dimension=2,
                )

        result = asyncio.run(run())
        self.assertTrue(result.asynchronous)
        self.assertEqual(result.written_node_count, 2)

    def test_failed_validation_discards_shadow_collection(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with self.assertRaises(RuntimeError):
                self.writer.build(
                    collection_name="knowledge_shadow_qdrant_bad_dim",
                    collection_metadata={},
                    nodes=self.nodes,
                    expected_dimension=3,
                )
        self.assertNotIn(
            "knowledge_shadow_qdrant_bad_dim",
            {item.name for item in self.client.get_collections().collections},
        )


class QdrantKnowledgeServiceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.chroma_dir = tempfile.TemporaryDirectory()
        self.qdrant_dir = tempfile.TemporaryDirectory()
        self.artifact_dir = tempfile.TemporaryDirectory()
        self.path_patch = patch.object(
            knowledge_module, "CHROMA_PATH", self.chroma_dir.name
        )
        self.config_patch = patch.dict(AI_CONFIG, {
            "vector_store_provider": "qdrant",
            "qdrant_mode": "local",
            "qdrant_path": self.qdrant_dir.name,
            "qdrant_batch_size": 2,
            "rag_release_smoke_required": False,
            "rag_embedding_cache_enabled": False,
        })
        self.path_patch.start()
        self.config_patch.start()
        self.chroma_service = None
        self.service = KnowledgeService(
            embedding_model="replaceable-embedding",
            markdown_converter=_MarkdownConverter(),
            embedding=_ServiceEmbedding(),
            artifact_root=self.artifact_dir.name,
        )

    def tearDown(self):
        if self.chroma_service is not None and self.chroma_service._client is not None:
            self.chroma_service._client = None
        if self.service._qdrant_client is not None:
            self.service._qdrant_client.close()
        self.config_patch.stop()
        self.path_patch.stop()
        self.artifact_dir.cleanup()
        self.qdrant_dir.cleanup()
        self.chroma_dir.cleanup()

    def test_stage_publish_search_and_rollback_across_providers(self):
        with patch.dict(AI_CONFIG, {"vector_store_provider": "chroma"}):
            self.chroma_service = KnowledgeService(
                embedding_model="replaceable-embedding",
                markdown_converter=_MarkdownConverter(),
                embedding=_ServiceEmbedding(),
                artifact_root=self.artifact_dir.name,
            )
            chroma_staged = self.chroma_service.stage_document_release(
                b"raw-baseline", "manual.md"
            )
            self.assertIsNone(self.chroma_service.publish_staged_release(
                chroma_staged["release_id"]
            ))
            self.assertEqual(
                self.chroma_service.active_vector_store_provider(), "chroma"
            )
            self.assertTrue(
                self.chroma_service.search_documents("工业异常", top_k=3)
            )

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            manifest = self.service.rebuild_shadow_from_active()
        staged = {"release_id": manifest["release_id"]}
        manifest = self.service.artifact_repository.releases.get(
            staged["release_id"]
        )
        self.assertEqual(
            manifest["indexing"]["vector_store_provider"], "qdrant"
        )
        self.assertEqual(
            manifest["indexing"]["point_id_schema_version"],
            POINT_ID_SCHEMA_VERSION,
        )
        self.assertEqual(manifest["vector_store"]["distance"], "cosine")

        previous = self.service.publish_staged_release(staged["release_id"])
        self.assertEqual(previous["vector_store_provider"], "chroma")
        pointer = self.service.artifact_repository.releases.active()
        self.assertEqual(pointer["vector_store_provider"], "qdrant")
        self.assertEqual(self.service.active_vector_store_provider(), "qdrant")
        results = self.service.search_documents("工业异常", top_k=3)
        self.assertTrue(results)
        self.assertEqual(results[0]["doc_id"], chroma_staged["doc_id"])

        self.assertTrue(self.service.rollback_published_release(
            staged["release_id"], previous
        ))
        self.assertEqual(self.service.active_vector_store_provider(), "chroma")
        rollback_results = self.service.search_documents("工业异常", top_k=3)
        self.assertTrue(rollback_results)
        self.assertEqual(rollback_results[0]["doc_id"], chroma_staged["doc_id"])

    def test_cold_bm25_uses_local_docstore_when_qdrant_is_unavailable(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            staged = self.service.stage_document_release(
                b"raw-qdrant", "manual.md"
            )
            self.service.publish_staged_release(staged["release_id"])

        cold_service = KnowledgeService(
            embedding_model="replaceable-embedding",
            markdown_converter=_MarkdownConverter(),
            embedding=_ServiceEmbedding(),
            artifact_root=self.artifact_dir.name,
        )
        unavailable = Mock()
        unavailable.get_collections.side_effect = RuntimeError(
            "qdrant unavailable"
        )
        cold_service._qdrant_client = unavailable

        with self.assertRaisesRegex(RuntimeError, "qdrant unavailable"):
            cold_service.search_documents("工业异常", top_k=3)

        results = cold_service.lexical_search(
            "工业异常检测",
            top_k=3,
            allowed_doc_ids={staged["doc_id"]},
        )

        self.assertTrue(results)
        self.assertEqual(results[0]["doc_id"], staged["doc_id"])
        unavailable.scroll.assert_not_called()

if __name__ == "__main__":
    unittest.main()
