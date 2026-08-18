import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock


BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from services.chat_service import ChatService  # noqa: E402
from services.rag.core.access import AccessPrincipal  # noqa: E402
from services.rag.operations.audit import RagAuditRecorder  # noqa: E402
from services.rag.search.lexical import BM25Index  # noqa: E402
from services.rag.search.reranking import CrossEncoderReranker  # noqa: E402
from services.rag.indexing.vector_store import ChromaVectorStore  # noqa: E402


class RagP6RetrievalTests(unittest.TestCase):
    def test_bm25_filters_before_top_k(self):
        records = [
            {"doc_id": "forbidden", "node_id": "f", "content": "watch nvidia-smi"},
            {"doc_id": "allowed", "node_id": "a", "content": "watch nvidia-smi GPU"},
        ]

        results = BM25Index(records).search(
            "watch nvidia-smi",
            top_k=1,
            allowed_doc_ids={"allowed"},
        )

        self.assertEqual([item["node_id"] for item in results], ["a"])

    def test_chroma_where_is_forwarded_to_vector_query(self):
        collection = Mock()
        collection.query.return_value = {
            "ids": [["n1"]],
            "documents": [["body"]],
            "metadatas": [[{"doc_id": "d1"}]],
            "distances": [[0.1]],
        }
        store = ChromaVectorStore(lambda: collection)

        store.query([1.0, 0.0], 50, where={"doc_id": {"$in": ["d1"]}})

        self.assertEqual(
            collection.query.call_args.kwargs["where"],
            {"doc_id": {"$in": ["d1"]}},
        )

    def test_cross_encoder_orders_candidates_and_has_metadata(self):
        model = Mock()
        model.predict.return_value = [0.1, 0.9]
        reranker = CrossEncoderReranker(
            model_name="local-test",
            enabled=True,
            timeout_seconds=1,
            model=model,
        )

        results, stats = asyncio.run(reranker.rerank(
            "query",
            [{"node_id": "a", "content": "A"}, {"node_id": "b", "content": "B"}],
            top_k=1,
        ))

        self.assertEqual(results[0]["node_id"], "b")
        self.assertEqual(results[0]["rerank_score"], 0.9)
        self.assertEqual(stats["mode"], "cross_encoder")

    def test_online_path_uses_authorized_overfetch(self):
        knowledge = Mock()
        knowledge.supports_authorized_retrieval = True
        knowledge.allowed_document_ids.return_value = {"allowed"}
        knowledge.asearch = AsyncMock(return_value=[{
            "node_id": "n1",
            "doc_id": "allowed",
            "content": "GPU 使用 nvidia-smi 查看。",
            "score": 0.9,
            "allowed_roles": "管理员,用户",
            "visibility": "internal",
        }])
        knowledge.alexical_search = AsyncMock(return_value=[])
        knowledge.current_release_id.return_value = "release-1"
        knowledge.embedding_provider = "dashscope"
        knowledge.embedding_model = "text-embedding-v2"
        knowledge.embedding_schema_version = "v1"
        service = ChatService(Mock(), knowledge)
        service.audit_recorder = RagAuditRecorder(enabled=False)

        packed = asyncio.run(service._aretrieve_packed_context(
            "GPU 怎么查看？",
            principal=AccessPrincipal(user_id=7, role="用户"),
        ))

        self.assertTrue(packed.entries)
        self.assertEqual(knowledge.asearch.call_args.kwargs["top_k"], 50)
        self.assertEqual(
            knowledge.asearch.call_args.kwargs["allowed_doc_ids"], {"allowed"}
        )


if __name__ == "__main__":
    unittest.main()
