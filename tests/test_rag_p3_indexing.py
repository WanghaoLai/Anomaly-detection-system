import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

import chromadb  # noqa: E402
from chromadb.config import Settings as ChromaSettings  # noqa: E402

import services.knowledge_service as knowledge_module  # noqa: E402
from services.knowledge_service import KnowledgeService  # noqa: E402
from services.rag.core.contracts import Node  # noqa: E402
from services.rag.indexing.embedding import LlamaIndexEmbeddingAdapter  # noqa: E402
from services.rag.indexing.writer import (  # noqa: E402
    INDEX_WRITER_SCHEMA_VERSION,
    LlamaIndexChromaIndexWriter,
)


class RecordingEmbedding:
    provider = "test-provider"
    schema_version = "test-embedding-v1"
    normalized = True

    def __init__(self, dimension=3):
        self.dimension = dimension
        self.document_calls = []
        self.query_calls = []
        self.fail = False

    def _vector(self, first):
        return [float(first)] + [0.0] * (self.dimension - 1)

    def embed_documents(self, texts):
        self.document_calls.append(list(texts))
        if self.fail:
            raise RuntimeError("故障注入: embedding")
        return [self._vector(1) for _ in texts]

    def embed_query(self, query):
        self.query_calls.append(query)
        return self._vector(2)

    def embed_queries(self, queries):
        self.query_calls.extend(queries)
        return [self._vector(2) for _ in queries]

    async def aembed_documents(self, texts):
        await asyncio.sleep(0)
        return self.embed_documents(texts)

    async def aembed_query(self, query):
        await asyncio.sleep(0)
        return self.embed_query(query)


class LlamaIndexIndexWriterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.client = chromadb.PersistentClient(
            path=self.directory.name,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self.backend = RecordingEmbedding(3)
        self.adapter = LlamaIndexEmbeddingAdapter(
            self.backend, model_name="replaceable-embedding", embed_batch_size=2
        )
        self.writer = LlamaIndexChromaIndexWriter(
            client_provider=lambda: self.client,
            embedding_adapter=self.adapter,
            insert_batch_size=2,
        )

    def tearDown(self):
        self.directory.cleanup()

    @staticmethod
    def _node(index=0):
        return Node(
            text=f"工业异常节点 {index}",
            metadata={
                "doc_id": "doc-p3",
                "chunk_index": index,
                "filename": "manual.md",
                "embedding_text_type": "document",
            },
            node_id=f"{index + 1:064x}",
        )

    def test_llama_embedding_distinguishes_document_and_query(self):
        documents = self.adapter.get_text_embedding_batch(["doc-a", "doc-b"])
        query = self.adapter.get_query_embedding("query-a")

        self.assertEqual(len(documents), 2)
        self.assertEqual(query, [2.0, 0.0, 0.0])
        self.assertEqual(self.backend.document_calls, [["doc-a", "doc-b"]])
        self.assertEqual(self.backend.query_calls, ["query-a"])

    def test_duplicate_node_is_written_once(self):
        node = self._node()
        result = self.writer.build(
            collection_name="knowledge_shadow_p3_duplicate",
            collection_metadata={"embedding_model": "replaceable-embedding"},
            nodes=[node, node],
            expected_dimension=3,
        )

        self.assertEqual(result.input_node_count, 2)
        self.assertEqual(result.written_node_count, 1)
        self.assertEqual(result.duplicate_node_count, 1)
        collection = self.client.get_collection(result.collection_name)
        self.assertEqual(collection.count(), 1)
        metadata = collection.get(include=["metadatas"])["metadatas"][0]
        self.assertEqual(metadata["doc_id"], "doc-p3")
        self.assertEqual(metadata["embedding_dim"], 3)

    def test_dimension_mismatch_is_blocked_before_collection_creation(self):
        with self.assertRaisesRegex(RuntimeError, "维度"):
            self.writer.build(
                collection_name="knowledge_shadow_p3_bad_dimension",
                collection_metadata={"embedding_model": "replaceable-embedding"},
                nodes=[self._node()],
                expected_dimension=1536,
            )

        self.assertNotIn(
            "knowledge_shadow_p3_bad_dimension",
            {item.name for item in self.client.list_collections()},
        )

    def test_vector_write_failure_removes_partial_shadow(self):
        with patch(
            "llama_index.vector_stores.chroma.ChromaVectorStore.add",
            side_effect=RuntimeError("故障注入: chroma write"),
        ):
            with self.assertRaisesRegex(RuntimeError, "chroma write"):
                self.writer.build(
                    collection_name="knowledge_shadow_p3_failed_write",
                    collection_metadata={"embedding_model": "replaceable-embedding"},
                    nodes=[self._node()],
                    expected_dimension=3,
                )

        self.assertNotIn(
            "knowledge_shadow_p3_failed_write",
            {item.name for item in self.client.list_collections()},
        )

    def test_complete_rebuild_keeps_same_node_set(self):
        nodes = [self._node(0), self._node(1), self._node(2)]
        first = self.writer.build(
            collection_name="knowledge_shadow_p3_rebuild_a",
            collection_metadata={"embedding_model": "replaceable-embedding"},
            nodes=nodes,
            expected_dimension=3,
        )
        second = self.writer.build(
            collection_name="knowledge_shadow_p3_rebuild_b",
            collection_metadata={"embedding_model": "replaceable-embedding"},
            nodes=nodes,
            expected_dimension=3,
        )

        self.assertEqual(first.node_ids, second.node_ids)
        self.assertEqual(first.written_node_count, second.written_node_count)
        self.assertNotEqual(first.collection_name, second.collection_name)

    def test_async_build_uses_async_embedding_path(self):
        async def run():
            return await self.writer.abuild(
                collection_name="knowledge_shadow_p3_async",
                collection_metadata={"embedding_model": "replaceable-embedding"},
                nodes=[self._node(0), self._node(1)],
                expected_dimension=3,
            )

        result = asyncio.run(run())

        self.assertTrue(result.asynchronous)
        self.assertEqual(result.written_node_count, 2)
        self.assertEqual(
            self.client.get_collection(result.collection_name).count(), 2
        )


class MarkdownConverter:
    def __init__(self):
        self.markdown = "# 工业手册\n\n异常检测平台配置。"

    def convert_stream(self, stream, **kwargs):
        stream.read()
        return SimpleNamespace(markdown=self.markdown)


class P3ServiceIntegrationTests(unittest.TestCase):
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
        self.path_patch = patch.object(
            knowledge_module, "CHROMA_PATH", self.chroma_dir.name
        )
        self.config_patch.start()
        self.path_patch.start()
        self.embedding = RecordingEmbedding(3)
        self.converter = MarkdownConverter()
        self.service = KnowledgeService(
            embedding_model="replaceable-embedding",
            markdown_converter=self.converter,
            embedding=self.embedding,
            artifact_root=self.artifact_dir.name,
        )

    def tearDown(self):
        self.path_patch.stop()
        self.config_patch.stop()
        self.artifact_dir.cleanup()
        self.chroma_dir.cleanup()

    def test_manifest_blue_green_rebuild_and_four_store_reconciliation(self):
        staged = self.service.stage_document_release(b"raw-p3", "manual.md")
        manifest = self.service.artifact_repository.releases.get(
            staged["release_id"]
        )

        self.assertEqual(manifest["indexing"]["framework"], "llamaindex")
        self.assertEqual(
            manifest["indexing"]["writer_schema_version"],
            INDEX_WRITER_SCHEMA_VERSION,
        )
        self.assertEqual(manifest["indexing"]["mode"], "blue_green_full_rebuild")
        self.assertEqual(manifest["indexing"]["duplicate_node_count"], 0)
        self.assertEqual(manifest["embedding"]["document_input_type"], "document")
        self.assertEqual(manifest["embedding"]["query_input_type"], "query")
        self.assertEqual(manifest["embedding"]["provider"], "test-provider")
        self.assertEqual(self.service.active_collection_name(), "knowledge_base")

        self.service.publish_staged_release(staged["release_id"])
        report = self.service.reconcile_metadata([{
            "id": 1,
            "filename": staged["doc_id"],
            "original_name": "manual.md",
            "file_size": staged["file_size"],
            "chunk_count": staged["chunk_count"],
        }])
        rebuilt = self.service.rebuild_shadow_from_active()

        self.assertTrue(report["healthy"], report["issues"])
        self.assertEqual(rebuilt["node_count"], manifest["node_count"])
        self.assertEqual(
            rebuilt["node_ids_sha256"], manifest["node_ids_sha256"]
        )
        self.assertEqual(
            self.service.active_collection_name(), staged["collection_name"]
        )

    def test_async_ingestion_and_write_failure_leave_active_release_unchanged(self):
        first = self.service.stage_document_release(b"raw-v1", "manual.md")
        self.service.publish_staged_release(first["release_id"])
        pointer_before = self.service.artifact_repository.releases.active()
        collections_before = {
            item.name for item in self.service.client.list_collections()
        }
        self.converter.markdown = "# 工业手册\n\n待发布的新版配置。"

        async def run():
            with patch(
                "llama_index.vector_stores.chroma.ChromaVectorStore.add",
                side_effect=RuntimeError("故障注入: async chroma write"),
            ):
                return await self.service.stage_document_release_async(
                    b"raw-v2", "manual.md"
                )

        with self.assertRaisesRegex(RuntimeError, "async chroma write"):
            asyncio.run(run())

        self.assertEqual(
            self.service.artifact_repository.releases.active(), pointer_before
        )
        self.assertEqual(
            {item.name for item in self.service.client.list_collections()},
            collections_before,
        )
        self.assertEqual(
            self.service.collection.count(), first["chunk_count"]
        )


if __name__ == "__main__":
    unittest.main()
