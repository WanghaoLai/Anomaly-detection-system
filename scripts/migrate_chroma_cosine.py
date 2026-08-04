"""一次性脚本：把 Chroma knowledge_base 重建为 cosine 距离度量。

背景：knowledge_service.py 早期未显式指定 hnsw:space，Chroma 默认 L2；
text-embedding-v2 按余弦相似度训练，二者向量空间语义不对齐，召回质量打折。
本脚本读出全部 chunk + 已有 embedding + metadata，删除旧 collection，按
cosine 重建后再写回，不重新调用 DashScope embedding。

用法：
    # 仅查看当前状态
    python3 scripts/migrate_chroma_cosine.py --dry-run
    # 执行迁移，并保留 JSON 备份
    python3 scripts/migrate_chroma_cosine.py --keep-backup
    # 即使已是 cosine 也强制重建
    python3 scripts/migrate_chroma_cosine.py --force
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fastapi-app"))

from services.knowledge_service import DOC_COLLECTION, KnowledgeService


def describe(svc: KnowledgeService) -> dict:
    names = [c.name for c in svc.client.list_collections()]
    if DOC_COLLECTION not in names:
        return {"exists": False, "name": DOC_COLLECTION}
    col = svc.client.get_collection(name=DOC_COLLECTION)
    return {
        "exists": True,
        "name": DOC_COLLECTION,
        "count": col.count(),
        "metadata": col.metadata,
        "space": (col.metadata or {}).get("hnsw:space", "<unset -> default L2>"),
    }


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印当前状态，不执行迁移")
    parser.add_argument("--force", action="store_true", help="即使已是 cosine 也强制重建")
    parser.add_argument("--keep-backup", action="store_true", help="迁移前把 chunk+embedding 导出到 JSON")
    args = parser.parse_args()

    svc = KnowledgeService()

    print("==== 迁移前 ====")
    print(describe(svc))

    if args.dry_run:
        print("\n--dry-run 模式，未执行任何改动。")
        return

    print("\n==== 执行迁移 ====")
    result = svc.migrate_to_cosine(force=args.force, keep_backup=args.keep_backup)
    print(result)

    print("\n==== 迁移后 ====")
    print(describe(svc))

    if not result.get("migrated"):
        return

    print("\n提示：如本次启用了 --keep-backup，备份 JSON 路径已在上面的结果中给出；")
    print(f"如需再次确认 embedding 模型一致性，请检查 AI_CONFIG.embedding_model = "
          f"{os.getenv('DASHSCOPE_EMBEDDING_MODEL', 'text-embedding-v2')}")


if __name__ == "__main__":
    main()
