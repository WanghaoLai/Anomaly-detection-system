"""校验多论文阶段 0 的冻结语料与黄金评测集。

默认只做可在 CI 中运行的静态契约检查。传入 ``--source-dir`` 后还会核对
15 个原始 PDF 的文件名、字节数和 SHA256；``--verify-text`` 进一步使用
PyPDF2 3.0.1 复算逐页文本，并确认每个黄金证据锚点确实出现在指定页。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = PROJECT_ROOT / "config" / "rag_multi_paper_corpus_v1.json"
EVAL_PATH = PROJECT_ROOT / "config" / "rag_multi_paper_eval_v1.json"
CONTRACT_PATH = PROJECT_ROOT / "config" / "rag_multi_paper_baseline_v1_contract.json"

REQUIRED_CATEGORIES = {
    "single_paper_fact",
    "cross_paper_comparison",
    "cross_paper_synthesis",
    "multi_turn",
    "exact_term",
    "cross_language",
    "table_figure",
    "negative",
    "permission",
    "conflicting_conclusions",
}
REQUIRED_QUESTION_FIELDS = {
    "id",
    "category",
    "question",
    "intent",
    "relevant_work_ids",
    "document_ids",
    "evidence_anchors",
    "required_aspects",
    "expected_claims",
    "forbidden_claims",
    "access_principal",
}
ACCESS_RANK = {"public": 0, "internal": 1, "admin": 2}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_static(
    corpus: dict[str, Any], dataset: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    documents = list(corpus.get("documents") or [])
    questions = list(dataset.get("questions") or [])

    if corpus.get("corpus_id") != dataset.get("corpus_id"):
        errors.append("语料与评测集 corpus_id 不一致")
    if len(documents) != 15:
        errors.append(f"冻结论文数应为 15，实际为 {len(documents)}")

    document_ids = [item.get("document_id") for item in documents]
    work_ids = [item.get("work_id") for item in documents]
    filenames = [item.get("filename") for item in documents]
    for label, values in (
        ("document_id", document_ids),
        ("work_id", work_ids),
        ("filename", filenames),
    ):
        if any(not isinstance(value, str) or not value for value in values):
            errors.append(f"语料存在空 {label}")
        if len(values) != len(set(values)):
            errors.append(f"语料 {label} 不唯一")

    docs_by_id = {item["document_id"]: item for item in documents if item.get("document_id")}
    docs_by_work = {item["work_id"]: item for item in documents if item.get("work_id")}
    required_doc_fields = {
        "document_id", "work_id", "filename", "title", "authors",
        "publication_year", "language", "access_level", "sha256",
        "byte_size", "page_count", "extracted_text_chars",
        "extracted_text_sha256", "diagnostics",
    }
    for doc in documents:
        missing = sorted(required_doc_fields - set(doc))
        if missing:
            errors.append(f"{doc.get('work_id')} 缺少字段: {missing}")
            continue
        digest = str(doc["sha256"])
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            errors.append(f"{doc['work_id']} 的 sha256 非法")
        if doc["document_id"] != f"paper-{digest[:16]}":
            errors.append(f"{doc['work_id']} 的 document_id 未由内容哈希生成")
        if doc["access_level"] not in ACCESS_RANK:
            errors.append(f"{doc['work_id']} 的 access_level 非法")
        for field in ("byte_size", "page_count", "extracted_text_chars"):
            if not isinstance(doc[field], int) or doc[field] <= 0:
                errors.append(f"{doc['work_id']} 的 {field} 必须为正整数")

    question_ids = [item.get("id") for item in questions]
    if len(question_ids) != len(set(question_ids)):
        errors.append("黄金问题 ID 不唯一")
    categories = Counter(item.get("category") for item in questions)
    missing_categories = sorted(REQUIRED_CATEGORIES - set(categories))
    if missing_categories:
        errors.append(f"黄金集缺少类别: {missing_categories}")

    principals = dataset.get("principals") or {}
    for case in questions:
        case_id = case.get("id", "[unknown]")
        missing = sorted(REQUIRED_QUESTION_FIELDS - set(case))
        if missing:
            errors.append(f"{case_id} 缺少字段: {missing}")
            continue
        for field in (
            "question", "intent", "required_aspects", "expected_claims",
        ):
            if not case[field]:
                errors.append(f"{case_id} 的 {field} 不能为空")
        if not isinstance(case["forbidden_claims"], list):
            errors.append(f"{case_id} 的 forbidden_claims 必须是数组")
        if case["access_principal"] not in principals:
            errors.append(f"{case_id} 引用了未知 access_principal")

        unknown_works = sorted(set(case["relevant_work_ids"]) - set(docs_by_work))
        unknown_docs = sorted(set(case["document_ids"]) - set(docs_by_id))
        if unknown_works:
            errors.append(f"{case_id} 引用了未知 work_id: {unknown_works}")
        if unknown_docs:
            errors.append(f"{case_id} 引用了未知 document_id: {unknown_docs}")

        expected_doc_ids = {
            docs_by_work[work_id]["document_id"]
            for work_id in case["relevant_work_ids"]
            if work_id in docs_by_work
        }
        if set(case["document_ids"]) != expected_doc_ids:
            errors.append(f"{case_id} 的 work_id 与 document_id 映射不一致")

        anchor_doc_ids: set[str] = set()
        for anchor in case["evidence_anchors"]:
            doc_id = anchor.get("document_id")
            anchor_doc_ids.add(doc_id)
            doc = docs_by_id.get(doc_id)
            if doc is None:
                errors.append(f"{case_id} 证据锚点引用未知文档 {doc_id}")
                continue
            page = anchor.get("page")
            if not isinstance(page, int) or not 1 <= page <= doc["page_count"]:
                errors.append(f"{case_id} 在 {doc_id} 的证据页码越界")
            terms = anchor.get("locator_terms")
            if not isinstance(terms, list) or not terms or any(not str(term).strip() for term in terms):
                errors.append(f"{case_id} 的证据 locator_terms 不能为空")
        if expected_doc_ids and not expected_doc_ids.issubset(anchor_doc_ids):
            errors.append(f"{case_id} 没有为每个相关文档提供证据锚点")

        principal = principals.get(case["access_principal"], {})
        clearance = principal.get("clearance")
        if clearance not in ACCESS_RANK:
            errors.append(f"{case_id} 主体 clearance 非法")
            continue
        overrides = case.get("corpus_acl_overrides") or {}
        unknown_override_works = sorted(set(overrides) - set(docs_by_work))
        if unknown_override_works:
            errors.append(f"{case_id} ACL 覆盖引用未知 work_id")
        for work_id in case["relevant_work_ids"]:
            if work_id not in docs_by_work:
                continue
            required = overrides.get(work_id, docs_by_work[work_id]["access_level"])
            if required not in ACCESS_RANK:
                errors.append(f"{case_id} ACL 级别非法: {required}")
            elif ACCESS_RANK[clearance] < ACCESS_RANK[required]:
                errors.append(f"{case_id} 把无权文档列为 relevant_work_ids")

    return errors


def validate_source(
    corpus: dict[str, Any],
    dataset: dict[str, Any],
    source_dir: Path,
    *,
    verify_text: bool,
) -> list[str]:
    errors: list[str] = []
    if not source_dir.is_dir():
        return [f"论文目录不存在: {source_dir}"]
    documents = list(corpus["documents"])
    expected_names = {item["filename"] for item in documents}
    actual_names = {path.name for path in source_dir.glob("*.pdf")}
    if actual_names != expected_names:
        errors.append(
            f"PDF 集合漂移: missing={sorted(expected_names - actual_names)} "
            f"added={sorted(actual_names - expected_names)}"
        )

    pages_by_document: dict[str, list[str]] = {}
    PdfReader = None
    if verify_text:
        try:
            import PyPDF2
            from PyPDF2 import PdfReader as Reader
        except ImportError:
            return ["--verify-text 需要 PyPDF2==3.0.1"]
        if PyPDF2.__version__ != corpus["extraction_profile"]["implementation_version"]:
            errors.append(
                "PyPDF2 版本与冻结提取器不一致: "
                f"expected={corpus['extraction_profile']['implementation_version']} "
                f"actual={PyPDF2.__version__}"
            )
        PdfReader = Reader

    for doc in documents:
        path = source_dir / doc["filename"]
        if not path.is_file():
            continue
        if path.stat().st_size != doc["byte_size"]:
            errors.append(f"{doc['work_id']} 字节数漂移")
        actual_hash = _sha256(path)
        if actual_hash != doc["sha256"]:
            errors.append(
                f"{doc['work_id']} SHA256 漂移: expected={doc['sha256']} actual={actual_hash}"
            )
        if PdfReader is None:
            continue
        try:
            reader = PdfReader(path)
            pages = [
                (page.extract_text() or "").replace("\x00", "").strip()
                for page in reader.pages
            ]
        except Exception as exc:  # pragma: no cover - depends on external PDFs
            errors.append(f"{doc['work_id']} 文本抽取失败: {exc}")
            continue
        pages_by_document[doc["document_id"]] = pages
        full_text = "\n".join(pages)
        if len(pages) != doc["page_count"]:
            errors.append(f"{doc['work_id']} 页数漂移")
        if len(full_text) != doc["extracted_text_chars"]:
            errors.append(f"{doc['work_id']} 抽取字符数漂移")
        if hashlib.sha256(full_text.encode("utf-8")).hexdigest() != doc["extracted_text_sha256"]:
            errors.append(f"{doc['work_id']} 抽取文本哈希漂移")

    if verify_text:
        docs_by_id = {
            item["document_id"]: item for item in corpus["documents"]
        }
        for case in dataset["questions"]:
            for anchor in case["evidence_anchors"]:
                anchor_doc = docs_by_id[anchor["document_id"]]
                if "manual_visual_review_required" in anchor_doc["diagnostics"]:
                    # 部分内嵌 CMap（当前为 GBK-EUC-H）会让 PyPDF2 产生稳定但
                    # 不可读的 mojibake。文件与抽取哈希仍校验，语义锚点由人工
                    # 视觉复核，避免把乱码匹配伪装成自动语义验证。
                    continue
                pages = pages_by_document.get(anchor["document_id"])
                if not pages or anchor["page"] > len(pages):
                    continue
                page_text = " ".join(pages[anchor["page"] - 1].split()).casefold()
                compact_page_text = "".join(page_text.split())
                for term in anchor["locator_terms"]:
                    normalized_term = " ".join(str(term).split()).casefold()
                    compact_term = "".join(normalized_term.split())
                    if (
                        normalized_term not in page_text
                        and compact_term not in compact_page_text
                    ):
                        errors.append(
                            f"{case['id']} 页级证据词未命中: "
                            f"document={anchor['document_id']} page={anchor['page']} term={term!r}"
                        )
    return errors


def validate_contract(
    corpus: dict[str, Any], dataset: dict[str, Any], contract: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    categories = dict(Counter(item["category"] for item in dataset["questions"]))
    actual = {
        "corpus_id": corpus.get("corpus_id"),
        "corpus_version": corpus.get("version"),
        "documents": len(corpus.get("documents") or []),
        "corpus_canonical_sha256": _canonical_sha256(corpus),
        "dataset_version": dataset.get("version"),
        "questions": len(dataset.get("questions") or []),
        "categories": categories,
        "dataset_canonical_sha256": _canonical_sha256(dataset),
    }
    for key, value in actual.items():
        if contract.get(key) != value:
            errors.append(
                f"冻结契约漂移 {key}: expected={contract.get(key)!r} actual={value!r}"
            )
    if contract.get("phase0_status") == "complete":
        baseline_path = PROJECT_ROOT / str(contract.get("baseline_path") or "")
        if not baseline_path.is_file():
            errors.append("阶段 0 标记完成但 baseline 文件不存在")
        elif _sha256(baseline_path) != contract.get("baseline_sha256"):
            errors.append("baseline_v1 文件哈希漂移")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, help="包含 15 个 PDF 的目录")
    parser.add_argument(
        "--verify-text",
        action="store_true",
        help="复算冻结抽取文本并验证所有页级证据词；必须同时传 --source-dir",
    )
    args = parser.parse_args()
    if args.verify_text and args.source_dir is None:
        parser.error("--verify-text 必须与 --source-dir 一起使用")

    corpus = _load_json(CORPUS_PATH)
    dataset = _load_json(EVAL_PATH)
    errors = validate_static(corpus, dataset)
    if CONTRACT_PATH.is_file():
        errors.extend(validate_contract(corpus, dataset, _load_json(CONTRACT_PATH)))
    else:
        errors.append(f"冻结契约不存在: {CONTRACT_PATH}")
    if args.source_dir is not None:
        errors.extend(
            validate_source(
                corpus, dataset, args.source_dir.resolve(), verify_text=args.verify_text
            )
        )

    if errors:
        print("多论文阶段 0 校验失败：")
        for error in errors:
            print(f"- {error}")
        return 1
    print(
        "多论文阶段 0 校验通过："
        f"{len(corpus['documents'])} 篇论文，{len(dataset['questions'])} 条黄金问题。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
