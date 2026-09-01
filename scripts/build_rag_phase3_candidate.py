"""Build the deterministic Phase 3 candidate review set from signed V0 evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "config" / "rag_golden_dataset_v0.json"
DEFAULT_OUTPUT = ROOT / "config" / "rag_phase3_candidate_v1.json"


GENERAL_QUESTIONS = (
    "请解释二分查找的基本原理。",
    "Python 列表推导式适合什么场景？",
    "HTTP 404 和 500 状态码有什么区别？",
    "Git rebase 与 merge 的主要区别是什么？",
    "Docker 容器和虚拟机有什么区别？",
    "SQL 索引为什么能提高查询速度？",
    "JSON 和 YAML 各有什么特点？",
    "如何计算一组数字的算术平均值？",
    "什么是公钥加密？",
    "Linux 文件权限中的 rwx 分别表示什么？",
    "TCP 和 UDP 的主要区别是什么？",
    "什么是 REST API？",
    "时间复杂度 O(n log n) 表示什么？",
    "如何理解机器学习中的过拟合？",
    "训练集、验证集和测试集分别有什么作用？",
    "精确率和召回率有什么区别？",
    "什么是标准差？",
    "进程和线程有什么区别？",
    "HTTPS 为什么比 HTTP 更安全？",
    "什么是数据库事务的原子性？",
)


AMBIGUOUS_GENERAL = (
    "CUDA 和 GPU 有什么区别？",
    "PyTorch 中自动求导的原理是什么？",
    "SSH 公钥认证的原理是什么？",
    "ZeroTier 主要用来做什么？",
    "异常检测常见的评价指标有哪些？",
    "Conda 环境有什么作用？",
    "nvidia-smi 会显示哪些信息？",
    "GPU 显存与普通内存有什么区别？",
    "数据集如何划分训练集和测试集？",
    "管理员权限意味着什么？",
)


AMBIGUOUS_KB = (
    ("rag_v0_001_1", "第一次接入时应该先做哪一步？"),
    ("rag_v0_002_1", "Windows 上第二种接入方式怎么配置？"),
    ("rag_v0_003_1", "Mac 上那个网络工具要怎么设置？"),
    ("rag_v0_004_1", "第一次登录后需要修改什么？"),
    ("rag_v0_005_1", "机器资源不够时这里应该怎么处理？"),
    ("rag_v0_006_1", "当前环境里的版本要怎么配套？"),
    ("rag_v0_007_1", "文件应该放到哪个共享位置？"),
    ("rag_v0_008_1", "代码拉不下来时这里要怎么处理？"),
    ("rag_v0_009_1", "任务失败以后到哪里找记录？"),
    ("rag_v0_010_1", "免密登录在这里要准备什么？"),
)


PRONOUN_QUESTIONS = (
    "关于它，还有哪些容易被忽略的限制？",
    "那部分的适用边界是什么？",
    "这个主题还需要满足哪些前置条件？",
    "它与常见做法相比有什么不同？",
    "其中最关键的注意事项是什么？",
)


FOLLOW_UP_QUESTIONS = (
    "继续说明实施时的关键注意事项。",
    "再说说有哪些明确限制。",
    "接着说明容易出错的环节。",
    "继续列出需要核对的关键条件。",
    "再补充一下适用边界。",
)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _topic(question: str) -> str:
    prefix = "根据知识库，"
    suffix = "有哪些明确、可核验的事实？"
    text = str(question)
    if text.startswith(prefix) and text.endswith(suffix):
        return text[len(prefix):-len(suffix)]
    raise ValueError(f"无法从 V0 问题提取主题: {question}")


def _review() -> dict[str, Any]:
    return {
        "status": "pending_human_review",
        "route_label_approved": False,
        "rewrite_target_approved": False,
        "evidence_approved": False,
        "notes": "",
    }


def _base_case(
    *,
    case_id: str,
    set_name: str,
    question: str,
    expected_mode: str,
    expected_route_stage: str,
    history: list[dict[str, str]] | None = None,
    source: dict[str, Any] | None = None,
    expected_retrieval_query: str | None = None,
) -> dict[str, Any]:
    return {
        "id": case_id,
        "set": set_name,
        "question": question,
        "history": list(history or []),
        "expected_mode": expected_mode,
        "expected_route_stage": expected_route_stage,
        "expected_retrieval_query": expected_retrieval_query or question,
        "expected_evidence": list((source or {}).get("expected_evidence") or []),
        "source_case_id": (source or {}).get("id"),
        "source_locator": (source or {}).get("source_locator"),
        "review": _review(),
    }


def build(baseline: dict[str, Any]) -> dict[str, Any]:
    if baseline.get("status") != "signed_baseline_v0":
        raise RuntimeError("Phase 3 候选集必须派生自已签署的 Baseline V0")
    by_id = {case["id"]: case for case in baseline["cases"]}
    exact = [case for case in baseline["cases"] if case["category"] == "exact_fact"]
    if len(exact) < 50:
        raise RuntimeError("Baseline V0 exact_fact 数量不足 50")

    cases: list[dict[str, Any]] = []
    for index, question in enumerate(GENERAL_QUESTIONS, start=1):
        cases.append(_base_case(
            case_id=f"rag_p3_general_{index:03d}",
            set_name="general",
            question=question,
            expected_mode="general",
            expected_route_stage="rule_general",
        ))

    for index, question in enumerate(AMBIGUOUS_GENERAL, start=1):
        cases.append(_base_case(
            case_id=f"rag_p3_ambiguous_general_{index:03d}",
            set_name="ambiguous",
            question=question,
            expected_mode="general",
            expected_route_stage="intent_classifier",
        ))
    for index, (source_id, question) in enumerate(AMBIGUOUS_KB, start=1):
        cases.append(_base_case(
            case_id=f"rag_p3_ambiguous_kb_{index:03d}",
            set_name="ambiguous",
            question=question,
            expected_mode="knowledge_base",
            expected_route_stage="intent_classifier",
            source=by_id[source_id],
        ))

    for index, source in enumerate(exact[10:30], start=1):
        topic = _topic(source["question"])
        current = PRONOUN_QUESTIONS[(index - 1) % len(PRONOUN_QUESTIONS)]
        cases.append(_base_case(
            case_id=f"rag_p3_pronoun_{index:03d}",
            set_name="pronoun",
            question=current,
            history=[{"role": "user", "content": source["question"]}],
            expected_mode="knowledge_base",
            expected_route_stage="intent_classifier",
            source=source,
            expected_retrieval_query=f"关于“{topic}”，{current}",
        ))

    for index, source in enumerate(exact[30:50], start=1):
        topic = _topic(source["question"])
        current = FOLLOW_UP_QUESTIONS[(index - 1) % len(FOLLOW_UP_QUESTIONS)]
        cases.append(_base_case(
            case_id=f"rag_p3_follow_up_{index:03d}",
            set_name="follow_up",
            question=current,
            history=[{"role": "user", "content": source["question"]}],
            expected_mode="knowledge_base",
            expected_route_stage="intent_classifier",
            source=source,
            expected_retrieval_query=f"关于“{topic}”，{current}",
        ))

    if len(cases) != 80 or len({case["id"] for case in cases}) != 80:
        raise AssertionError("Phase 3 候选集必须包含 80 个唯一 Case")
    projection = json.dumps(cases, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return {
        "schema_version": "rag-phase3-candidate-v1",
        "name": "industrial-anomaly-rag-phase3-candidate",
        "version": "P3-CANDIDATE-V1",
        "status": "candidate_pending_human_review",
        "base_dataset": {
            "name": baseline["name"],
            "version": baseline["version"],
            "release_id": baseline["release_id"],
            "question_fingerprint": baseline["question_fingerprint"],
        },
        "policy": {
            "ambiguous_fallback": "knowledge_base",
            "classifier_failure_fallback": "knowledge_base",
            "history_user_turn_limit": 2,
            "production_model_features_default_enabled": False,
        },
        "coverage": {
            "total": len(cases),
            "set_counts": dict(sorted(Counter(case["set"] for case in cases).items())),
            "expected_mode_counts": dict(sorted(Counter(case["expected_mode"] for case in cases).items())),
            "route_stage_counts": dict(sorted(Counter(case["expected_route_stage"] for case in cases).items())),
        },
        "candidate_fingerprint": hashlib.sha256(projection).hexdigest(),
        "review_requirements": {
            "all_cases_must_be_reviewed": True,
            "required_case_fields": [
                "route_label_approved",
                "rewrite_target_approved",
                "evidence_approved",
            ],
            "signoff_status": "signed_phase3_dataset",
        },
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 Phase 3 人工审核候选集")
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    result = build(_load(Path(args.baseline).resolve()))
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(output),
        "status": result["status"],
        "coverage": result["coverage"],
        "candidate_fingerprint": result["candidate_fingerprint"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
