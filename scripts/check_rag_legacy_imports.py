"""检查生产代码是否重新依赖 RAG 旧平铺导入路径。"""

from __future__ import annotations

import ast
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "fastapi-app"
LEGACY_MODULES = frozenset({
    "access", "artifacts", "audit", "context", "contracts", "embeddings",
    "generation", "grounding", "ingestion", "lexical", "llamaindex_indexing",
    "llamaindex_parser", "loaders", "reranking", "retrieval", "splitters",
    "sse", "vector_store",
})
LEGACY_FILES = {
    BACKEND_ROOT / "services" / "rag" / f"{name}.py"
    for name in LEGACY_MODULES
}


def _legacy_name(module: str | None) -> str | None:
    if not module:
        return None
    parts = module.split(".")
    if len(parts) >= 2 and parts[-2] == "rag" and parts[-1] in LEGACY_MODULES:
        return parts[-1]
    return None


def scan_production_imports() -> list[dict]:
    violations: list[dict] = []
    for path in sorted(BACKEND_ROOT.rglob("*.py")):
        if path in LEGACY_FILES or "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.ImportFrom):
                module = node.module
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    legacy = _legacy_name(alias.name)
                    if legacy:
                        violations.append({
                            "file": str(path.relative_to(PROJECT_ROOT)),
                            "line": node.lineno,
                            "module": alias.name,
                        })
                continue
            legacy = _legacy_name(module)
            if legacy:
                violations.append({
                    "file": str(path.relative_to(PROJECT_ROOT)),
                    "line": node.lineno,
                    "module": module,
                })
    return violations


def main() -> int:
    violations = scan_production_imports()
    print(json.dumps({
        "ok": not violations,
        "scope": "fastapi-app production code",
        "legacy_modules": sorted(LEGACY_MODULES),
        "violations": violations,
    }, ensure_ascii=False, indent=2))
    return 0 if not violations else 1


if __name__ == "__main__":
    raise SystemExit(main())
