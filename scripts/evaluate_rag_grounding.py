"""P5 Grounding 安全门禁的确定性验收与可选真实 Qwen 探测。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from services.chat_service import ChatService  # noqa: E402
from services.knowledge_service import KnowledgeService  # noqa: E402
from services.llm_service import LLMService  # noqa: E402
from services.rag.core.access import AccessPrincipal, KnowledgeAccessPolicy  # noqa: E402
from services.rag.answering.context import ContextPacker, ContextPackingPolicy  # noqa: E402
from services.rag.answering.grounding import (  # noqa: E402
    GroundedAnswerValidator,
    GroundingValidationError,
)
from services.rag.operations.sse import PUBLIC_FAILURE_MESSAGES  # noqa: E402
from settings import AI_CONFIG  # noqa: E402


def _packed(content: str, node_id: str = "node-1"):
    return ContextPacker(ContextPackingPolicy(
        token_budget=500,
        min_body_tokens=8,
        max_body_tokens=300,
    )).pack([{
        "node_id": node_id,
        "filename": "验收手册.md",
        "section_path": "操作",
        "position": "L1-L5",
        "content": content,
    }])


def offline_evaluate() -> dict:
    validator = GroundedAnswerValidator(
        minimum_faithfulness=float(
            AI_CONFIG.get("rag_faithfulness_threshold", 0.90)
        ),
        minimum_lexical_support=float(
            AI_CONFIG.get("rag_claim_lexical_support", 0.08)
        ),
    )
    fixtures = [
        ("GPU 状态使用 `nvidia-smi` 查看。", "GPU 状态可执行 `nvidia-smi` 查看。"),
        ("每两秒刷新可执行 `watch -n 2 nvidia-smi`。", "每两秒刷新可执行 `watch -n 2 nvidia-smi`。"),
        ("磁盘空间使用 `df -h` 查看。", "磁盘空间可使用 `df -h` 查看。"),
        ("共享目录 `/home/share/` 对全员可见。", "`/home/share/` 是全员可见的共享目录。"),
        ("每个用户默认存储空间为 400 GB。", "每个用户默认有 400 GB 存储空间。"),
    ]
    published = []
    valid_citations = 0
    total_citations = 0
    for index, (evidence, claim) in enumerate(fixtures, start=1):
        packed = _packed(evidence, f"node-{index}")
        answer = validator.validate({
            "mode": "knowledge_base",
            "refusal": False,
            "claims": [{"text": claim, "citations": ["K1"]}],
        }, packed)
        published.append(answer)
        total_citations += len(answer.citations)
        valid_citations += sum(
            citation in packed.citation_map for citation in answer.citations
        )

    invalid_cases = [
        {
            "mode": "knowledge_base",
            "refusal": False,
            "claims": [{"text": "伪造结论", "citations": ["K99"]}],
        },
        {
            "mode": "knowledge_base",
            "refusal": False,
            "claims": [{
                "text": "执行 `rm -rf /data` 清理 GPU。",
                "citations": ["K1"],
            }],
        },
    ]
    invalid_blocked = 0
    for value in invalid_cases:
        try:
            validator.validate(value, _packed("GPU 使用 nvidia-smi 查看。"))
        except GroundingValidationError:
            invalid_blocked += 1

    access = KnowledgeAccessPolicy()
    restricted = {
        "visibility": "admin_only",
        "allowed_roles": "管理员",
        "content": "管理员机密",
    }
    bypass_blocked = not access.is_allowed(
        restricted, AccessPrincipal(user_id=7, role="用户")
    )
    no_knowledge = validator.refusal("no_knowledge")
    citation_validity = valid_citations / total_citations
    faithfulness = sum(item.faithfulness for item in published) / len(published)
    metrics = {
        "citation_validity": round(citation_validity, 4),
        "faithfulness": round(faithfulness, 4),
        "faithfulness_threshold": validator.minimum_faithfulness,
        "no_knowledge_refusal_rate": 1.0 if no_knowledge.refusal else 0.0,
        "invalid_output_block_rate": round(
            invalid_blocked / len(invalid_cases), 4
        ),
        "permission_bypass_block_rate": 1.0 if bypass_blocked else 0.0,
        "explicit_failure_states": sorted({
            "completed", "refused", "llm_timeout", "generation_failed",
            "llm_protocol_error", "stream_disconnected",
        }),
        "public_failure_messages_complete": all(
            code in PUBLIC_FAILURE_MESSAGES
            for code in (
                "llm_timeout", "generation_failed", "llm_protocol_error",
                "stream_disconnected",
            )
        ),
    }
    ok = (
        metrics["citation_validity"] == 1.0
        and metrics["faithfulness"] >= validator.minimum_faithfulness
        and metrics["no_knowledge_refusal_rate"] == 1.0
        and metrics["invalid_output_block_rate"] == 1.0
        and metrics["permission_bypass_block_rate"] == 1.0
        and metrics["public_failure_messages_complete"]
    )
    return {"ok": ok, "mode": "offline_contract", "metrics": metrics}


async def live_probe(question: str) -> dict:
    service = ChatService(
        LLMService(
            AI_CONFIG["dashscope_api_key"],
            AI_CONFIG["model"],
        ),
        KnowledgeService(),
    )
    answer = await service.answer(
        question,
        [],
        principal={"user_id": 1, "role": "用户"},
    )
    return {
        "mode": answer.mode,
        "status": answer.status,
        "refusal": answer.refusal,
        "citations": list(answer.citations),
        "faithfulness": answer.faithfulness,
        "reason_code": answer.reason_code,
        "text": answer.text,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="验收 P5 Grounding 门禁")
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--question",
        default="如何每两秒持续刷新实验室服务器的 GPU 使用情况？",
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "reports" / "rag_p5_grounding_eval.json"),
    )
    args = parser.parse_args()
    report = offline_evaluate()
    if args.live:
        report["live_probe"] = asyncio.run(live_probe(args.question))
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        "ok": report["ok"],
        "output": str(output),
        "metrics": report["metrics"],
        "live_probe": report.get("live_probe"),
    }, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
