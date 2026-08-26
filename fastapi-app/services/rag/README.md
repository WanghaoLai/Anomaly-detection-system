# RAG 模块结构

当前分层、重构前后目录和迁移规则见
[`ARCHITECTURE_REFACTOR.md`](./ARCHITECTURE_REFACTOR.md)。新代码应优先从
`core`、`document`、`indexing`、`search`、`answering`、`operations` 六个功能包
导入；根目录平铺模块只作为兼容路径保留。

P0 已冻结行为、应用接口和分层规则；变更本模块前先阅读
[`P0_BASELINE.md`](./P0_BASELINE.md)，并运行离线契约检查。
多论文改进阶段 0 的冻结语料、黄金评测集和校验方式见
[`MULTI_PAPER_BASELINE_V1.md`](./MULTI_PAPER_BASELINE_V1.md)。
文档上传、DocStore 和影子索引发布见
[`P1_INGESTION.md`](./P1_INGESTION.md)；LlamaIndex TextNode 解析见
[`P2_NODES.md`](./P2_NODES.md)；LlamaIndex Embedding 与蓝绿向量写入见
[`P3_INDEXING.md`](./P3_INDEXING.md)；重排后的严格上下文装箱见
[`P4_CONTEXT.md`](./P4_CONTEXT.md)；Qwen Grounding、引用验证和权限隔离见
[`P5_GROUNDED_GENERATION.md`](./P5_GROUNDED_GENERATION.md)。

本目录按 RAG 的两个本质阶段组织代码：先把外部资料变成可检索的语义节点，
再用查询召回节点并把有限上下文交给生成模型。核心应用算法不直接依赖厂商 SDK；
现阶段适配器与遗留装配门面承担外部依赖。

```text
上传文件
  -> DocumentLoader
  -> DocumentPreprocessor
  -> NodeParser
  -> EmbeddingModel
  -> VectorStore

用户问题 + 对话历史
  -> QueryTransformer
  -> Retriever / ResultSelector
  -> ContextBuilder
  -> PromptBuilder
  -> ResponseGenerator
```

## 文件职责

- `contracts.py`：`Document`、`Node`、检索结果及各阶段 Protocol。
- `loaders.py`：MarkItDown 加载适配器和格式相关的 PDF 清洗。
- `splitters.py`：纯 Markdown 语义分块和 Token 预算算法。
- `llamaindex_parser.py`：LlamaIndex `NodeParser` 适配、稳定 TextNode ID、
  引用与位置 metadata。
- `embeddings.py`：DashScope 文档/查询向量适配器。
- `llamaindex_indexing.py`：LlamaIndex `VectorStoreIndex` 与 Chroma 的蓝绿写入适配器。
- `vector_store.py`：Chroma collection 的最薄读写适配层。
- `retrieval.py`：dense 阈值过滤、去重、字面召回和 RRF 融合。
- `lexical.py`：按 release 缓存的 BM25 倒排索引，支持授权 doc_id 过滤。
- `reranking.py`：可选本地 Cross-Encoder 精排，超时/依赖失败回退 RRF。
- `audit.py`：检索 release、候选分数、引用映射、Token 和耗时的 MySQL 审计。
- `context.py`：查询感知 Context Packing、硬预算、引用映射和原子命令保护。
- `access.py`：可信身份、规范化文档 ACL 和服务端 Node 权限过滤。
- `grounding.py`：模式路由、知识提示、结构化 Claim 契约与引用/忠实度门禁。
- `sse.py`：SSE 编码和超时、生成失败、协议失败、连接断开的公开状态。
- `generation.py`：历史查询补全、P0 上下文兼容入口、提示组装和生成编排。
- `ingestion.py`：加载、清洗、切分三个纯阶段的组合编排。

`KnowledgeService` 继续负责上传替换、Chroma/MySQL 补偿和索引迁移等应用事务；
`ChatService` 继续作为 API 调用方的兼容门面。两者不再实现核心算法，而是委托
本目录组件。

## LlamaIndex 边界

P2 已将 `MarkdownNodeParser` 实现为 LlamaIndex `NodeParser`，并输出原生
`TextNode`。对业务层仍实现 `contracts.NodeParser.parse(Document)`，因此
`KnowledgeService`、API、DocStore 和 Chroma 不导入 LlamaIndex 类型。

P3 已将 Embedding 批处理与影子索引写入交给 LlamaIndex，业务层仅依赖
`EmbeddingModel` 和 `VectorIndexWriter` 端口。DashScope 和 Chroma 只是默认
适配器，可独立替换。

后续 embedding、vector store 或 retriever 可以继续通过 Protocol 单独替换，
不要求同时切换所有组件。

## P6 授权检索链路

在线知识模式先从当前 Release Manifest 解析授权 `doc_id`，
以 Chroma `where={"doc_id":{"$in":...}}` 在向量 Top-K 前过滤。
默认授权 Dense Top-50 与 BM25 Top-50 做 RRF，候选上限 100；
可选 Cross-Encoder 最终保留 4～8 条。Chroma 与 Embedding 的同步
SDK 调用移入 `asyncio.to_thread`，Qwen 客户端复用连接池并具备
总 deadline、重试、熔断和取消传播。

开启审计前应用 `migrations/010_rag_retrieval_audit.sql`；每次检索保存
release、模型/Prompt 版本、候选分数、K 到来源的映射、估算 Token
和分阶段耗时。

阶段 0 多论文最终冻结语料、黄金评测集、release 与评测基线见
`MULTI_PAPER_BASELINE_V1.md`。
