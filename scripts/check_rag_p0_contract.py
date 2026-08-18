"""离线校验 P0 RAG 行为、应用端口、API 和分层边界。

默认检查不调用数据库、Chroma、DashScope 或生成模型，适合每次提交和 CI。
``--check-index`` 额外核对本机 Chroma 快照；``--report`` 可对新生成的联网
评测报告做零退化比较。
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import importlib.util
import inspect
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "fastapi-app"
CONTRACT_PATH = PROJECT_ROOT / "config" / "rag_p0_contract.json"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# settings.py 在任何入口都会执行 JWT 安全校验。契约检查不启动服务器，缺少
# 本地 .env 时使用测试专用值，避免让离线 CI 依赖生产密钥。
os.environ.setdefault(
    "JWT_SECRET_KEY",
    "rag-p0-contract-check-only-not-for-runtime-0000000000000000",
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _nested(mapping: dict[str, Any], dotted_key: str) -> Any:
    value: Any = mapping
    for part in dotted_key.split("."):
        value = value[part]
    return value


def _schema_from_operation(openapi: dict, operation: dict, content_type: str) -> dict:
    body = operation.get("requestBody") or {}
    schema = ((body.get("content") or {}).get(content_type) or {}).get("schema") or {}
    reference = schema.get("$ref")
    if not reference:
        return schema
    name = reference.rsplit("/", 1)[-1]
    return openapi["components"]["schemas"][name]


def check_dataset(contract: dict) -> list[str]:
    errors: list[str] = []
    expected = contract["dataset"]
    path = PROJECT_ROOT / expected["path"]
    if not path.is_file():
        return [f"评测集不存在: {path}"]
    actual_hash = _sha256(path)
    if actual_hash != expected["sha256"]:
        errors.append(
            f"评测集 SHA256 漂移: expected={expected['sha256']} actual={actual_hash}"
        )
    dataset = _load_json(path)
    questions = list(dataset.get("questions") or [])
    if len(questions) != expected["questions"]:
        errors.append(
            f"评测问题数漂移: expected={expected['questions']} actual={len(questions)}"
        )
    counts = Counter(item.get("category") for item in questions)
    if dict(counts) != expected["categories"]:
        errors.append(
            f"评测分类数量漂移: expected={expected['categories']} actual={dict(counts)}"
        )
    ids = [item.get("id") for item in questions]
    if len(ids) != len(set(ids)):
        errors.append("评测问题 ID 不唯一")
    return errors


def check_runtime_configuration(contract: dict) -> list[str]:
    from settings import AI_CONFIG

    errors: list[str] = []
    expected = contract["runtime_configuration"]
    key_mapping = {
        "embedding_model": "embedding_model",
        "rag_candidate_k": "rag_candidate_k",
        "rag_final_k": "rag_final_k",
        "rag_score_threshold": "rag_score_threshold",
        "rag_hybrid_enabled": "rag_hybrid_enabled",
        "rag_lexical_min_score": "rag_lexical_min_score",
        "rag_context_tokens": "rag_context_tokens",
        "rag_query_history_turns": "rag_query_history_turns",
        "rag_chunk_tokens": "rag_chunk_tokens",
        "rag_overlap_tokens": "rag_overlap_tokens",
    }
    for contract_key, config_key in key_mapping.items():
        actual = AI_CONFIG.get(config_key)
        if actual != expected[contract_key]:
            errors.append(
                f"RAG 配置漂移 {config_key}: expected={expected[contract_key]!r} "
                f"actual={actual!r}"
            )
    return errors


def check_prompt(contract: dict) -> list[str]:
    from services.chat_service import ChatService

    actual = hashlib.sha256(ChatService.SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    expected = contract["behavior"]["system_prompt_sha256"]
    if actual != expected:
        return [f"系统提示词漂移: expected={expected} actual={actual}"]
    return []


def check_report(contract: dict, report_path: Path) -> list[str]:
    errors: list[str] = []
    if not report_path.is_file():
        return [f"基线对比报告不存在: {report_path}"]
    report = _load_json(report_path)
    expected = contract["behavior"]
    tolerance = float(expected.get("comparison_tolerance", 0.0))

    if report.get("index") != expected["index_snapshot"]:
        errors.append(
            f"索引快照漂移: expected={expected['index_snapshot']} "
            f"actual={report.get('index')}"
        )
    if report.get("dataset", {}).get("questions") != contract["dataset"]["questions"]:
        errors.append("报告中的评测问题数与 P0 契约不一致")
    metrics = report.get("metrics") or {}
    for key, baseline in expected["metrics"].items():
        try:
            actual = float(_nested(metrics, key))
        except (KeyError, TypeError, ValueError):
            errors.append(f"报告缺少指标: {key}")
            continue
        if actual + tolerance < float(baseline):
            errors.append(
                f"指标退化 {key}: baseline={baseline} actual={actual} "
                f"tolerance={tolerance}"
            )
    return errors


def check_local_index(contract: dict) -> list[str]:
    from services.knowledge_service import KnowledgeService

    service = KnowledgeService()
    report = service.validate_embedding_config()
    errors = [f"本地索引契约不一致: {issue}" for issue in report["issues"]]
    actual = {
        "embedding_model": service.embedding_model,
        "documents": len({
            str((metadata or {}).get("doc_id"))
            for metadata in (
                service.collection.get(include=["metadatas"]).get("metadatas") or []
            )
            if (metadata or {}).get("doc_id")
        }),
        "chunks": service.collection.count(),
    }
    expected = contract["behavior"]["index_snapshot"]
    if actual != expected:
        errors.append(f"本地索引快照漂移: expected={expected} actual={actual}")
    return errors


def check_application_ports(contract: dict) -> list[str]:
    contracts_module = importlib.import_module("services.rag.core.contracts")
    errors: list[str] = []
    for protocol_name, methods in contract["application_ports"].items():
        protocol = getattr(contracts_module, protocol_name, None)
        if protocol is None:
            errors.append(f"应用端口已删除: {protocol_name}")
            continue
        for method_name, expected_parameters in methods.items():
            method = getattr(protocol, method_name, None)
            if method is None:
                errors.append(f"应用端口方法已删除: {protocol_name}.{method_name}")
                continue
            actual_parameters = list(inspect.signature(method).parameters)
            if actual_parameters != expected_parameters:
                errors.append(
                    f"应用端口签名漂移 {protocol_name}.{method_name}: "
                    f"expected={expected_parameters} actual={actual_parameters}"
                )
    return errors


def check_api_contract(contract: dict) -> list[str]:
    from main import app

    openapi = app.openapi()
    expected_routes = {
        (item["path"], item["method"].lower()) for item in contract["api_routes"]
    }
    actual_routes = {
        (path, method)
        for path, operations in openapi["paths"].items()
        if path.startswith("/knowledge")
        or path.startswith("/chat")
        or path.startswith("/admin/chat")
        for method in operations
        if method in {"get", "post", "put", "delete", "patch"}
    }
    errors: list[str] = []
    if actual_routes != expected_routes:
        missing = sorted(expected_routes - actual_routes)
        added = sorted(actual_routes - expected_routes)
        errors.append(f"RAG API 路由漂移: missing={missing} added={added}")

    for item in contract["api_routes"]:
        path = item["path"]
        method = item["method"].lower()
        operation = (openapi["paths"].get(path) or {}).get(method)
        if operation is None:
            continue
        authenticated = bool(operation.get("security"))
        if authenticated != item["authenticated"]:
            errors.append(
                f"API 认证契约漂移 {method.upper()} {path}: "
                f"expected={item['authenticated']} actual={authenticated}"
            )
        expected_path_parameters = sorted(item.get("path_parameters") or [])
        actual_path_parameters = sorted(
            parameter["name"]
            for parameter in operation.get("parameters") or []
            if parameter.get("in") == "path" and parameter.get("required") is True
        )
        if actual_path_parameters != expected_path_parameters:
            errors.append(
                f"API 路径参数漂移 {method.upper()} {path}: "
                f"expected={expected_path_parameters} actual={actual_path_parameters}"
            )
        content_type = item.get("request_content_type")
        if content_type:
            content = ((operation.get("requestBody") or {}).get("content") or {})
            if content_type not in content:
                errors.append(
                    f"API 请求类型漂移 {method.upper()} {path}: "
                    f"expected={content_type} actual={sorted(content)}"
                )
                continue
            schema = _schema_from_operation(openapi, operation, content_type)
            actual_required = sorted(schema.get("required") or [])
            expected_required = sorted(item.get("required_fields") or [])
            if actual_required != expected_required:
                errors.append(
                    f"API 必填字段漂移 {method.upper()} {path}: "
                    f"expected={expected_required} actual={actual_required}"
                )
    return errors


def _import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def check_layering(_: dict) -> list[str]:
    from services.rag.layers import (
        APPLICATION_MODULES,
        DOMAIN_MODULES,
        FORBIDDEN_APPLICATION_IMPORTS,
    )

    errors: list[str] = []
    for module_name in (*DOMAIN_MODULES, *APPLICATION_MODULES):
        spec = importlib.util.find_spec(module_name)
        if spec is None or not spec.origin:
            errors.append(f"分层模块不存在: {module_name}")
            continue
        imports = _import_roots(Path(spec.origin))
        forbidden = sorted(imports & FORBIDDEN_APPLICATION_IMPORTS)
        if forbidden:
            errors.append(f"{module_name} 越层依赖厂商 SDK: {forbidden}")
    return errors


def run_checks(
    *,
    contract_path: Path = CONTRACT_PATH,
    report_path: Path | None = None,
    check_index: bool = False,
) -> dict[str, Any]:
    contract = _load_json(contract_path)
    checks = {
        "dataset": check_dataset(contract),
        "runtime_configuration": check_runtime_configuration(contract),
        "system_prompt": check_prompt(contract),
        "application_ports": check_application_ports(contract),
        "api_contract": check_api_contract(contract),
        "layering": check_layering(contract),
    }
    if report_path is not None:
        checks["behavior_report"] = check_report(contract, report_path)
    if check_index:
        checks["local_index"] = check_local_index(contract)
    errors = [error for values in checks.values() for error in values]
    return {
        "ok": not errors,
        "contract": str(contract_path),
        "checks": {
            name: "passed" if not values else "failed" for name, values in checks.items()
        },
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 RAG P0 行为与接口契约")
    parser.add_argument("--contract", default=str(CONTRACT_PATH))
    parser.add_argument("--report", default=None)
    parser.add_argument("--check-index", action="store_true")
    args = parser.parse_args()
    result = run_checks(
        contract_path=Path(args.contract).resolve(),
        report_path=Path(args.report).resolve() if args.report else None,
        check_index=args.check_index,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
