import sys
import unittest
from pathlib import Path
from unittest.mock import Mock


BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from services.chat_service import ChatService  # noqa: E402
from services.knowledge_service import _approx_token_len  # noqa: E402


class ChatServiceRagLoggingTests(unittest.TestCase):
    def test_retrieval_failure_is_logged_before_fallback(self):
        knowledge = Mock()
        knowledge.search.side_effect = RuntimeError("chroma unavailable")
        service = ChatService(Mock(), knowledge)

        with self.assertLogs("services.chat_service", level="ERROR") as captured:
            context = service._get_rag_context("查询")

        self.assertEqual(context, "")
        self.assertIn("RAG 检索失败并进入无知识拒答", "\n".join(captured.output))

    def test_retrieval_success_logs_scores_without_raw_query(self):
        knowledge = Mock()
        knowledge.search.return_value = [{
            "content": "答案",
            "filename": "manual.md",
            "chunk_index": 2,
            "score": 0.9,
        }]
        service = ChatService(Mock(), knowledge)
        service.rag_hybrid_enabled = False

        with self.assertLogs("services.chat_service", level="INFO") as captured:
            context = service._get_rag_context("敏感查询原文")

        logs = "\n".join(captured.output)
        self.assertIn("RAG 检索完成", logs)
        self.assertIn("top_scores=[0.9]", logs)
        self.assertNotIn("敏感查询原文", logs)
        self.assertIn("manual.md", context)
        self.assertIn("[K1]", context)
        knowledge.search.assert_called_once_with("敏感查询原文", top_k=8)

    def test_online_hybrid_adds_lexical_match_and_keeps_four(self):
        knowledge = Mock()
        knowledge.search.return_value = [{
            "content": "一般性的 GPU 说明",
            "doc_id": "dense",
            "chunk_index": 0,
            "score": 0.19,
        }]
        knowledge.list_document_chunks.return_value = [{
            "content": "持续刷新显卡状态：watch -n 2 nvidia-smi",
            "filename": "manual.md",
            "doc_id": "exact",
            "chunk_index": 3,
        }]
        service = ChatService(Mock(), knowledge)
        service.rag_score_threshold = 0.20
        service.rag_hybrid_enabled = True

        context = service._get_rag_context("watch -n 2 nvidia-smi 命令是什么？")

        self.assertIn("watch -n 2 nvidia-smi", context)
        self.assertIn("[K1]", context)
        knowledge.list_document_chunks.assert_called_once_with()

    def test_hybrid_lexical_failure_falls_back_to_dense(self):
        knowledge = Mock()
        knowledge.search.return_value = [{
            "content": "dense 答案",
            "filename": "manual.md",
            "doc_id": "dense",
            "chunk_index": 0,
            "score": 0.80,
        }]
        knowledge.list_document_chunks.side_effect = RuntimeError("read failed")
        service = ChatService(Mock(), knowledge)

        with self.assertLogs("services.chat_service", level="WARNING") as captured:
            context = service._get_rag_context("查询")

        self.assertIn("dense 答案", context)
        self.assertIn("已降级为 dense", "\n".join(captured.output))

    def test_candidate_selection_filters_threshold_deduplicates_and_keeps_four(self):
        service = ChatService(Mock(), Mock())
        service.rag_score_threshold = 0.35
        service.rag_final_k = 4
        repeated = "重复段落" * 20
        candidates = [
            {"content": repeated, "score": 0.95, "doc_id": "a", "chunk_index": 0},
            {"content": repeated, "score": 0.90, "doc_id": "a", "chunk_index": 1},
            {"content": "第一条有效内容", "score": 0.80, "doc_id": "b", "chunk_index": 0},
            {"content": "第二条有效内容", "score": 0.70, "doc_id": "c", "chunk_index": 0},
            {"content": "第三条有效内容", "score": 0.60, "doc_id": "d", "chunk_index": 0},
            {"content": "第四条有效内容", "score": 0.50, "doc_id": "e", "chunk_index": 0},
            {"content": "低分内容", "score": 0.20, "doc_id": "f", "chunk_index": 0},
        ]

        selected, stats = service._select_rag_results(candidates)

        self.assertEqual(len(selected), 4)
        self.assertEqual(stats["candidates"], 7)
        self.assertEqual(stats["threshold_passed"], 6)
        self.assertEqual(stats["deduplicated"], 5)
        self.assertEqual([item["doc_id"] for item in selected], ["a", "b", "c", "d"])

    def test_short_follow_up_is_completed_with_recent_user_questions(self):
        service = ChatService(Mock(), Mock())
        history = [
            {"role": "user", "content": "Windows 如何配置 ZeroTier？"},
            {"role": "assistant", "content": "请先安装客户端。"},
            {"role": "user", "content": "服务端如何批准设备？"},
            {"role": "assistant", "content": "由管理员批准。"},
        ]

        query = service._build_retrieval_query("那 macOS 呢？", history)

        self.assertIn("Windows 如何配置 ZeroTier", query)
        self.assertIn("服务端如何批准设备", query)
        self.assertIn("当前问题：那 macOS 呢？", query)
        self.assertNotIn("请先安装客户端", query)

    def test_complete_standalone_question_is_not_polluted_by_history(self):
        service = ChatService(Mock(), Mock())
        question = "PBAS 算法中的 threshold 参数如何影响异常检测结果？"

        query = service._build_retrieval_query(
            question,
            [{"role": "user", "content": "如何登录服务器？"}],
        )

        self.assertEqual(query, question)

    def test_numbered_context_respects_total_token_budget(self):
        service = ChatService(Mock(), Mock())
        service.rag_context_tokens = 100
        results = [
            {
                "content": "甲" * 100,
                "filename": "manual.md",
                "heading_path": "配置",
                "score": 0.9,
            },
            {
                "content": "乙" * 100,
                "filename": "manual.md",
                "heading_path": "排障",
                "score": 0.8,
            },
        ]

        context, used_tokens = service._build_numbered_context(results)

        self.assertIn("[K1] 来源：manual.md / 配置", context)
        self.assertIn("内容已按上下文预算截断", context)
        self.assertNotIn("[K2]", context)
        self.assertLessEqual(used_tokens, service.rag_context_tokens)
        self.assertLessEqual(_approx_token_len(context), service.rag_context_tokens)


if __name__ == "__main__":
    unittest.main()
