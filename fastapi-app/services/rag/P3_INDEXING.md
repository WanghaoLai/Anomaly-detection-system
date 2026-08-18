# RAG P3 Embedding 与蓝绿向量索引

## 第一性原则

Embedding 和向量库都是可重建的派生物。只有原文件、统一 Document、
稳定 Node 和已发布 Manifest 是可审计事实。因此 P3 不对在线 collection
做就地追加：每次都从 DocStore 构建一个完整绿色版，校验后只切换一个
原子指针。

## 核心组件

| 组件 | 职责 | 可替换性 |
| --- | --- | --- |
| `EmbeddingModel` | 应用层文档/查询向量端口 | DashScope 只是默认实现 |
| `LlamaIndexEmbeddingAdapter` | 把 `EmbeddingModel` 映射到 LlamaIndex `BaseEmbedding` | 不包含 DashScope SDK 类型 |
| `VectorIndexWriter` | 框架无关的蓝绿索引写入端口 | 可实现 Milvus/pgvector/ES 写入器 |
| `LlamaIndexChromaIndexWriter` | `embed_nodes` + `VectorStoreIndex` + Chroma 集成 | Chroma SDK 仅在此模块中 |
| `AsyncIngestionExecutor` | 用信号量限制全量入库并发，将同步 SDK 移出事件循环 | 默认并发度 1 |
| `ReleaseManifestStore` | 固化节点集合、Embedding 契约、写入器版本与基线指针 | 与向量库无关 |

## Embedding 区分

- 文档 Node 调用 LlamaIndex `get_text_embedding_batch`，底层 DashScope
  明确传入 `text_type=document`。
- 检索查询通过 `get_query_embeddings`，底层明确传入
  `text_type=query`。
- Node 所有内部 metadata 均排除在 embedding 文本之外，向量只表示正文语义。
- 模型、provider、schema、归一化策略和维度同时写入 collection、
  Node metadata 和 Manifest；任意一处不一致即阻断。

## 索引构建流程

1. 从当前 catalog 指向的 DocStore 读取全部 Node，不从旧 Chroma 复制向量。
2. 按 Node ID 去重；同 ID 但正文或 metadata 不同立即失败。
3. LlamaIndex `embed_nodes/async_embed_nodes` 批量生成文档向量。
4. 在创建 collection 前检查向量数量、数值有效性、零范数和维度。
5. 只有 Embedding 全部成功后才创建 `knowledge_shadow_<release_id>`。
6. 已携带 embedding 的 TextNode 交给 `VectorStoreIndex`，由它分批写入
   LlamaIndex Chroma vector store。
7. 反读 collection，检查 ID 唯一性、集合等价性和实际维度，再写 Manifest。
8. MySQL 事务内更新文档指针、CAS 切换发布指针并做四方对账。

## Manifest 契约

P3 Manifest 在 P1 字段之上增加：

```json
{
  "embedding": {
    "provider": "dashscope",
    "model": "text-embedding-v2",
    "schema_version": "dashscope-text-embedding-v1",
    "dimension": 1536,
    "normalized": true,
    "document_input_type": "document",
    "query_input_type": "query",
    "manager": "LlamaIndexEmbeddingAdapter"
  },
  "indexing": {
    "framework": "llamaindex",
    "writer": "VectorStoreIndex",
    "writer_schema_version": "llamaindex-blue-green-index-v1",
    "vector_store_provider": "chroma",
    "mode": "blue_green_full_rebuild",
    "docstore_node_count": 19,
    "prewrite_duplicate_count": 0,
    "input_node_count": 19,
    "written_node_count": 19,
    "duplicate_node_count": 0,
    "embedding_batches": 1,
    "write_batches": 1,
    "async_capable": true
  }
}
```

发布时会再比较 Manifest、collection metadata、Chroma 实际向量和 Node ID 集合。

## 蓝绿与失败隔离

| 失败阶段 | 处理 |
| --- | --- |
| 文档转换/Node 解析 | 不创建候选 collection，当前指针不变 |
| Embedding 网络或数值错误 | 在 collection 创建前阻断 |
| 模型/provider/schema/维度不一致 | 不允许构建或发布 |
| Chroma 部分写入 | 删除整个失败的影子 collection |
| Manifest 写入失败 | 删除影子 collection，不产生可发布版 |
| 并发发布 | `base_guard` CAS 拒绝过期候选版 |
| MySQL 提交失败 | 恢复旧发布指针，旧 collection 不删除 |

异步上传不采用无跟踪的 `create_task`。API 等待影子版构建完成后才进入
MySQL/发布事务；进程取消或异常最多留下不可变原文件/DocStore，绝不改变在线版。

## 验收

```bash
python3 -m pytest tests/test_rag_p3_indexing.py -q
python3 scripts/rebuild_rag_shadow.py
python3 scripts/check_rag_p0_contract.py
```

测试包含完整重建、Node 去重、维度阻断、Embedding/Chroma 故障注入、
异步写入、蓝绿指针保护与 MySQL/文件/DocStore/Chroma 对账。

## 当前环境验收结果（2026-08-18）

- 全量测试：`112 passed`；P0 数据集、运行配置、Prompt、端口、API、分层和
  本地索引契约全部通过。
- 真实 P3 release：`1a3bdc5e82f045cfa18d79faa1206aea`，14 个 DocStore
  Node 全部写入、无重复，Embedding 维度 1536。
- 冻结 40 问评测：Dense Hit@8 `0.975`、Dense Hit@4 `0.95`、Hybrid
  Hit@4 `0.975`、MRR `0.805`，与 P0/P1 基线一致。
- 发布后对账：MySQL 1 个文档/14 个块，DocStore 1 个文档/14 个 Node，
  Chroma 1 个文档/14 个向量，问题数 0。
- 当前文档属于 P1 前的历史导入，无法补造原始二进制，文件状态为
  `legacy_unavailable`；所有 P1+ 新上传均要求原文件存在、哈希一致，否则对账失败。
