# RAG 模块结构

本目录按 RAG 的两个本质阶段组织代码：先把外部资料变成可检索的语义节点，
再用查询召回节点并把有限上下文交给生成模型。厂商 SDK 只存在于适配器中。

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
- `splitters.py`：Markdown 标题感知、Token 预算和段落重叠切分。
- `embeddings.py`：DashScope 文档/查询向量适配器。
- `vector_store.py`：Chroma collection 的最薄读写适配层。
- `retrieval.py`：dense 阈值过滤、去重、字面召回和 RRF 融合。
- `generation.py`：历史查询补全、引用上下文预算、提示组装和生成编排。
- `ingestion.py`：加载、清洗、切分三个纯阶段的组合编排。

`KnowledgeService` 继续负责上传替换、Chroma/MySQL 补偿和索引迁移等应用事务；
`ChatService` 继续作为 API 调用方的兼容门面。两者不再实现核心算法，而是委托
本目录组件。

## 后续迁移到 LlamaIndex

迁移时保持 API 门面和应用事务不变，按顺序替换下列适配器即可：

1. 将 `Document` / `Node` 映射为 LlamaIndex 对应对象；
2. 用 LlamaIndex reader 与 node parser 替换 loader/parser；
3. 用 LlamaIndex embedding 与 vector store integration 替换基础设施适配器；
4. 用 LlamaIndex retriever/query engine 替换 `HybridResultSelector` 和生成编排。

各阶段通过 Protocol 和稳定 metadata 交接，迁移不要求同时切换所有组件。
