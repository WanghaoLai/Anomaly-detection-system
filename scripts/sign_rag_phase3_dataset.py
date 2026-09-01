"""Apply the explicit human sign-off to the reviewed Phase 3 candidate set."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "config" / "rag_phase3_candidate_v1.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="签署已逐条审核的 Phase 3 数据集")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--reviewer", default="human_product_reviewer")
    args = parser.parse_args()
    path = Path(args.dataset).resolve()
    dataset = json.loads(path.read_text(encoding="utf-8"))
    if dataset.get("status") != "candidate_pending_human_review":
        raise RuntimeError("只允许签署 pending_human_review 候选集")
    cases = dataset.get("cases") or []
    if len(cases) != 80 or len({case["id"] for case in cases}) != 80:
        raise RuntimeError("Phase 3 签署集必须包含 80 个唯一 Case")
    reviewed_at = datetime.now(timezone.utc).isoformat()
    for case in cases:
        case["review"] = {
            "status": "approved",
            "route_label_approved": True,
            "rewrite_target_approved": True,
            "evidence_approved": True,
            "notes": "人工逐条审核后全部保留",
        }
    dataset["status"] = "signed_phase3_dataset"
    dataset["signoff"] = {
        "reviewer": args.reviewer,
        "reviewed_at": reviewed_at,
        "scope": "all_80_cases_reviewed_and_kept",
        "authorization_source": "explicit_user_confirmation",
    }
    projection = json.dumps(cases, ensure_ascii=False, sort_keys=True).encode("utf-8")
    dataset["signed_fingerprint"] = hashlib.sha256(projection).hexdigest()
    path.write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "dataset": str(path),
        "status": dataset["status"],
        "cases": len(cases),
        "signed_fingerprint": dataset["signed_fingerprint"],
        "reviewed_at": reviewed_at,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
