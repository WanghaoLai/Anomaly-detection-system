import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

from tortoise import Tortoise


BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from services.chat_service import ChatService  # noqa: E402
from services.rag.operations import (  # noqa: E402
    RagAuditRecorder,
    RagRequestDeadlineExceeded,
    aggregate_trace_metrics,
)


class Phase5DeadlineTests(unittest.TestCase):
    def test_total_deadline_has_stable_error_and_publishes_no_content(self):
        service = ChatService(Mock(), Mock())
        service.audit_recorder = RagAuditRecorder(enabled=False)
        service.rag_request_deadline_seconds = 0.01

        async def slow_resolution(_message, _history):
            await asyncio.sleep(0.05)

        service._aresolve_query = slow_resolution

        async def consume():
            events = []
            with self.assertRaises(RagRequestDeadlineExceeded) as captured:
                async for event in service.process_message_events("问题", []):
                    events.append(event)
            return events, captured.exception

        events, error = asyncio.run(consume())

        self.assertEqual(error.code, "request_deadline_exceeded")
        self.assertFalse(any(item.get("type") == "content" for item in events))


class Phase5MetricsTests(unittest.TestCase):
    def test_metrics_are_observe_only_and_require_time_and_volume(self):
        records = [
            {
                "status": "completed",
                "mode": "knowledge_base",
                "retrieval_config": {"selection_mode": "bm25_fallback"},
                "stage_durations_ms": {"request_total": 120.0, "dense": 5.0},
            },
            {
                "status": "failed",
                "error_code": "request_deadline_exceeded",
                "mode": "knowledge_base",
                "retrieval_config": {"selection_mode": "retrieval_unavailable"},
                "stage_durations_ms": {"request_total": 75000.0},
            },
            {"status": "started", "mode": "pending"},
        ]

        metrics = aggregate_trace_metrics(records, window_days=7)

        self.assertEqual(metrics["policy"], "observe_only")
        self.assertEqual(metrics["valid_requests"], 2)
        self.assertEqual(metrics["rates"]["failure"], 0.5)
        self.assertEqual(metrics["rates"]["deadline_exceeded"], 0.5)
        self.assertEqual(metrics["degradation_counts"]["bm25_fallback"], 1)
        self.assertEqual(metrics["latency"]["request_total"]["p95_ms"], 75000.0)
        self.assertFalse(
            metrics["hard_slo_review_readiness"]["ready_for_human_review"]
        )


class Phase5AuditMergeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(
            db_url="sqlite://:memory:",
            modules={"models": ["models"]},
        )
        await Tortoise.generate_schemas()

    async def asyncTearDown(self):
        await Tortoise.close_connections()
        await Tortoise._reset_apps()

    async def test_same_trace_merges_stage_durations(self):
        from models import RagRetrievalTrace

        recorder = RagAuditRecorder(enabled=True)
        trace_id = "00000000-0000-0000-0000-000000000005"
        await recorder.record({
            "id": trace_id,
            "query_hash": "a" * 64,
            "mode": "pending",
            "status": "started",
            "stage_durations_ms": {"router": 1.0},
        })
        await recorder.record({
            "id": trace_id,
            "query_hash": "a" * 64,
            "mode": "knowledge_base",
            "status": "completed",
            "stage_durations_ms": {"dense": 2.0},
        })

        row = await RagRetrievalTrace.get(id=trace_id)
        self.assertEqual(
            row.stage_durations_ms,
            {"router": 1.0, "dense": 2.0},
        )


if __name__ == "__main__":
    unittest.main()
