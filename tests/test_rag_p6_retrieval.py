import asyncio
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from services.chat_service import ChatService  # noqa: E402
from services.rag.core.access import AccessPrincipal  # noqa: E402
from services.rag.operations.audit import RagAuditRecorder  # noqa: E402
from services.rag.search.lexical import BM25Index, ReleaseBM25Cache  # noqa: E402
from services.rag.search.reranking import CrossEncoderReranker  # noqa: E402
from services.rag.indexing.vector_store import ChromaVectorStore  # noqa: E402


class RagP6RetrievalTests(unittest.TestCase):
    def test_release_bm25_cache_does_not_reload_snapshot_on_hit(self):
        active = {"release_id": "release-1"}
        loaded = []

        def snapshot(release_id):
            loaded.append(release_id)
            return [{
                "doc_id": "doc-1",
                "node_id": f"node-{release_id}",
                "content": "GPU 使用 nvidia-smi 查看。",
            }]

        cache = ReleaseBM25Cache(
            lambda: active["release_id"],
            snapshot,
        )

        cache.search("nvidia-smi", top_k=3, allowed_doc_ids={"doc-1"})
        cache.search("GPU", top_k=3, allowed_doc_ids={"doc-1"})
        self.assertEqual(loaded, ["release-1"])

        active["release_id"] = "release-2"
        cache.search("GPU", top_k=3, allowed_doc_ids={"doc-1"})
        self.assertEqual(loaded, ["release-1", "release-2"])

    def test_release_bm25_cache_concurrent_cold_build_loads_once(self):
        load_count = 0
        count_lock = threading.Lock()

        def snapshot(_release_id):
            nonlocal load_count
            with count_lock:
                load_count += 1
            time.sleep(0.05)
            return [{
                "doc_id": "doc-1",
                "node_id": "node-1",
                "content": "工业异常检测配置。",
            }]

        cache = ReleaseBM25Cache(lambda: "release-1", snapshot)
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: cache.index(), range(8)))

        self.assertEqual(load_count, 1)
        self.assertTrue(all(release_id == "release-1" for release_id, _ in results))
        self.assertEqual(len({id(index) for _, index in results}), 1)

    def test_waiting_cache_reader_observes_release_switch(self):
        active = {"release_id": "release-1"}
        cache = ReleaseBM25Cache(
            lambda: active["release_id"],
            lambda release_id: [{
                "doc_id": "doc-1",
                "node_id": f"node-{release_id}",
                "content": "工业异常检测配置。",
            }],
        )
        cache.index()

        executor = ThreadPoolExecutor(max_workers=1)
        cache._lock.acquire()
        lock_held = True
        try:
            future = executor.submit(cache.index)
            time.sleep(0.02)
            active["release_id"] = "release-2"
            cache._lock.release()
            lock_held = False
            release_id, index = future.result(timeout=1)
        finally:
            if lock_held:
                cache._lock.release()
            executor.shutdown(wait=True)

        self.assertEqual(release_id, "release-2")
        self.assertEqual(index.records[0]["node_id"], "node-release-2")

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
        self.assertEqual(stats["input_count"], 2)
        self.assertEqual(stats["output_count"], 1)
        self.assertFalse(stats["fallback"])
        self.assertIsNone(stats["fallback_reason"])
        self.assertGreaterEqual(stats["elapsed_ms"], 0.0)

    def test_cross_encoder_passes_approved_max_length_to_loader(self):
        reranker = CrossEncoderReranker(
            model_name="local-test",
            enabled=True,
            timeout_seconds=1,
            max_length=256,
        )
        loaded = Mock()
        cross_encoder = Mock(return_value=loaded)
        fake_module = Mock(CrossEncoder=cross_encoder)
        with patch.dict(sys.modules, {"sentence_transformers": fake_module}):
            self.assertIs(reranker._get_model(), loaded)

        cross_encoder.assert_called_once_with("local-test", max_length=256)

    def test_cross_encoder_timeout_falls_back_to_rrf_order(self):
        model = Mock()

        def slow_predict(_pairs):
            time.sleep(0.05)
            return [0.9, 0.1]

        model.predict.side_effect = slow_predict
        reranker = CrossEncoderReranker(
            model_name="local-test",
            enabled=True,
            timeout_seconds=0.01,
            model=model,
        )

        results, stats = asyncio.run(reranker.rerank(
            "query",
            [{"node_id": "a", "content": "A"}, {"node_id": "b", "content": "B"}],
            top_k=1,
        ))

        self.assertEqual(results[0]["node_id"], "a")
        self.assertEqual(stats["mode"], "rrf_fallback")
        self.assertTrue(stats["fallback"])
        self.assertEqual(stats["fallback_reason"], "timeout")

        _, busy_stats = asyncio.run(reranker.rerank(
            "query-2",
            [{"node_id": "a", "content": "A"}],
            top_k=1,
        ))
        self.assertEqual(busy_stats["fallback_reason"], "busy_after_timeout")
        self.assertEqual(model.predict.call_count, 1)

    def test_cross_encoder_error_falls_back_to_rrf_order(self):
        model = Mock()
        model.predict.side_effect = RuntimeError("broken model")
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

        self.assertEqual(results[0]["node_id"], "a")
        self.assertEqual(stats["mode"], "rrf_fallback")
        self.assertTrue(stats["fallback"])
        self.assertEqual(stats["fallback_reason"], "RuntimeError")

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

    def test_dense_failure_falls_back_to_authorized_bm25(self):
        knowledge = Mock()
        knowledge.supports_authorized_retrieval = True
        knowledge.allowed_document_ids.return_value = {"allowed"}
        knowledge.asearch = AsyncMock(side_effect=RuntimeError("vector unavailable"))
        knowledge.alexical_search = AsyncMock(return_value=[{
            "node_id": "n-bm25",
            "doc_id": "allowed",
            "filename": "manual.md",
            "content": "使用 nvidia-smi 查看 GPU 状态。",
            "bm25_score": 3.0,
            "allowed_roles": "管理员,用户",
            "visibility": "internal",
        }])
        knowledge.current_release_id.return_value = "release-1"
        service = ChatService(Mock(), knowledge)
        service.audit_recorder = RagAuditRecorder(enabled=False)

        packed = asyncio.run(service._aretrieve_packed_context(
            "GPU 怎么查看？",
            principal=AccessPrincipal(user_id=7, role="用户"),
        ))

        self.assertTrue(packed.entries)
        self.assertEqual(packed.entries[0].node_id, "n-bm25")
        knowledge.alexical_search.assert_awaited_once()

    def test_both_retrieval_branches_failure_returns_no_knowledge(self):
        knowledge = Mock()
        knowledge.supports_authorized_retrieval = True
        knowledge.allowed_document_ids.return_value = {"allowed"}
        knowledge.asearch = AsyncMock(side_effect=RuntimeError("vector unavailable"))
        knowledge.alexical_search = AsyncMock(side_effect=RuntimeError("bm25 unavailable"))
        knowledge.current_release_id.return_value = "release-1"
        service = ChatService(Mock(), knowledge)
        service.audit_recorder = RagAuditRecorder(enabled=False)

        packed = asyncio.run(service._aretrieve_packed_context(
            "GPU 怎么查看？",
            principal=AccessPrincipal(user_id=7, role="用户"),
        ))

        self.assertFalse(packed.entries)


if __name__ == "__main__":
    unittest.main()
