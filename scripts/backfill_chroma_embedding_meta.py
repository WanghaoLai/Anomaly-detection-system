"""一次性脚本：为 Chroma knowledge_base 既有 chunk 补写 embedding 元信息。

背景：knowledge_service.py 此前未把 embedding_model / embedding_dim 写入 chunk
metadata，AI_CONFIG.embedding_model 一旦换型号，新写入的 chunk 与旧 chunk 会落入
不同向量空间，检索沉默劣化且不报错。本脚本只更新 metadata，不重新向 DashScope
请求 embedding；前提是既有 chunk 都来自当前 AI_CONFIG.embedding_model。

用法：
    # 先看当前校验状态
    python3 scripts/backfill_chroma_embedding_meta.py --dry-run
    # 执行 backfill（默认取 AI_CONFIG.embedding_model）
    python3 scripts/backfill_chroma_embedding_meta.py
    # 显式指定既有 chunk 来源模型
    python3 scripts/backfill_chroma_embedding_meta.py --model text-embedding-v3
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fastapi-app"))

from services.knowledge_service import KnowledgeService


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印当前校验状态，不修改")
    parser.add_argument("--model", default=None, help="覆盖回填使用的模型名（默认取 AI_CONFIG.embedding_model）")
    args = parser.parse_args()

    svc = KnowledgeService(embedding_model=args.model) if args.model else KnowledgeService()

    print("==== backfill 前 ====")
    print(svc.validate_embedding_config())

    if args.dry_run:
        print("\n--dry-run 模式，未执行任何改动。")
        return

    print("\n==== 执行 backfill ====")
    result = svc.backfill_embedding_metadata(model=args.model)
    print(result)

    print("\n==== backfill 后 ====")
    print(svc.validate_embedding_config())


if __name__ == "__main__":
    main()
