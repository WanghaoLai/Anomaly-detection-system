# RAG 架构重构说明

## 重构目标

本次重构以“处理阶段”和“依赖方向”组织代码，而不是按历史开发批次平铺文件。
`ChatService`、`KnowledgeService`、`LLMService` 仍是稳定的外部门面；API、测试和脚本的
旧导入路径继续可用。当前发布指针、DocStore、Node ID、向量集合和模型配置均不迁移。

依赖方向固定为：

```text
API
  -> ChatService / KnowledgeService / LLMService（兼容门面与装配）
  -> answering / search / document（应用阶段）
  -> core（领域对象与端口）
  -> indexing / operations（外部能力与运行保障）
```

应用算法不能创建 DashScope、Chroma、MarkItDown、LlamaIndex 或 ORM 对象；这些对象由
门面装配后通过端口传入。

## 重构前结构和职责

```text
services/
├── chat_service.py          # 模式、权限、召回、融合、精排、上下文、生成、审计
├── knowledge_service.py     # 上传事务 + 解析副本 + 分块副本 + 索引 + 检索 + 对账
├── llm_service.py           # LLM 错误 + 结果 + 熔断 + 重试 + DashScope HTTP/SDK
└── rag/
    ├── contracts.py
    ├── access.py
    ├── loaders.py
    ├── splitters.py
    ├── llamaindex_parser.py
    ├── ingestion.py
    ├── artifacts.py
    ├── embeddings.py
    ├── llamaindex_indexing.py
    ├── vector_store.py
    ├── retrieval.py
    ├── lexical.py
    ├── reranking.py
    ├── context.py
    ├── generation.py
    ├── grounding.py
    ├── audit.py
    └── sse.py
```

### 原文件职责盘点

| 文件 | 类/函数职责 | 原问题 |
| --- | --- | --- |
| `contracts.py` | Document、Node、Embedding/Vector/Generator Protocol | 合理，但没有稳定的分层入口 |
| `loaders.py` | MarkItDown、PDF 清理、文档预处理 | 与 `knowledge_service.py` 存在完整旧副本 |
| `splitters.py` | 标题解析、Token 估算、语义块聚合 | `knowledge_service.py` 仍定义另一套分块 |
| `llamaindex_parser.py` | TextNode、稳定 ID、位置 metadata | 与加载/切分文件平铺，阶段关系不直观 |
| `artifacts.py` | 原文件、DocStore、Manifest | 与在线索引适配器混放在同一层级 |
| `embeddings.py` | DashScope Embedding 和 LlamaIndex Adapter | 领域端口与厂商实现缺少目录隔离 |
| `llamaindex_indexing.py` | LlamaIndex/Chroma 蓝绿写入 | 文件名暴露具体组合，调用方需知道装配细节 |
| `retrieval.py` | 阈值、去重、RRF | 在线检索编排却仍在 `ChatService` |
| `lexical.py` | BM25 与发布缓存 | 与向量召回缺少统一 search 边界 |
| `reranking.py` | Cross-Encoder 精排与回退 | 由 `ChatService` 直接创建和调度 |
| `context.py` | Context Packing | 与生成模块平铺，调用阶段不明确 |
| `generation.py` | 查询补全、旧 Prompt、生成 Pipeline | 同时包含多个抽象层次 |
| `grounding.py` | 模式、结构化 Claim、引用/忠实度验证 | 合理，但没有 answering 聚合入口 |
| `audit.py`、`sse.py` | 审计和传输状态 | 运行保障能力与核心算法平铺 |

## 主要架构问题

1. **服务门面过重**：`ChatService` 不只是门面，还实现完整授权检索流水线；运行参数、
   业务策略和审计字段全部耦合在一个类中。
2. **重复实现**：`KnowledgeService` 曾重复定义 PDF 清理、标题解析、Token 估算和分块，
   文件尾部又把其中部分名称覆盖到 `rag` 实现，形成“看起来有两套、实际混用一套”的
   高风险状态。
3. **厂商依赖边界模糊**：`KnowledgeService` 直接装配 Chroma、DashScope、MarkItDown；
   `LLMService` 同时定义模型无关错误和 DashScope 实现。
4. **目录无法表达执行链**：从平铺文件名不能直观看出加载→切分→索引→召回→精排→
   上下文→生成的顺序。
5. **兼容路径被当作实现路径**：测试、脚本和服务分别从不同的平铺模块导入，移动一个
   文件会产生大量同步修改。
6. **审计和算法耦合**：检索算法直接知道 ORM 审计字段，难以独立替换或压测。

## 重构后结构

```text
services/rag/
├── core/
│   └── __init__.py                 # Document/Node/Ports/ACL 稳定入口
├── document/
│   ├── markdown.py                 # 共享 Markdown 语法定义
│   ├── loading.py                  # 加载与格式预处理
│   ├── splitting.py                # Markdown/Token 分块
│   ├── parsing.py                  # TextNode 和稳定 ID
│   ├── pipeline.py                 # 文档阶段编排
│   └── storage.py                  # 原文件/DocStore/Manifest
├── indexing/
│   ├── embedding.py                # Embedding 适配器
│   ├── vector_store.py             # 向量存储适配器
│   └── writer.py                   # LlamaIndex 蓝绿写入
├── search/
│   ├── retrieval.py                # Dense/RRF 选择策略
│   ├── lexical.py                  # BM25
│   ├── reranking.py                # Cross-Encoder
│   └── pipeline.py                 # ACL→召回→融合→精排→装箱→审计
├── answering/
│   ├── context.py                  # Context Packing
│   ├── prompting.py                # 查询补全与提示编排
│   ├── grounding.py                # 模式、Claim、引用和 Faithfulness
│   └── llm_types.py                # 模型无关错误、结果和熔断
├── operations/
│   ├── audit.py                    # 审计端口/实现入口
│   └── sse.py                      # SSE 生命周期
├── _compat.py                       # 兼容导出的唯一实现
├── contracts.py ... sse.py         # 纯 re-export，兼容期保留
└── layers.py                       # 自动化依赖守卫
```

## 新职责边界

- `ChatService`：身份和调用参数适配、普通/知识模式入口、兼容私有方法；不实现授权检索
  算法。
- `AuthorizedRetrievalPipeline`：唯一在线检索编排；接收已经装配好的 Knowledge 端口、
  ACL、Selector、Reranker、Packer 和 Audit Recorder。
- `KnowledgeService`：上传/删除/发布事务、依赖装配、兼容查询 API；解析和分块只委托
  `document` 层。
- `LLMService`：DashScope Qwen 适配器和旧公开入口；模型无关错误、结果、熔断语义位于
  `answering.llm_types`。
- `core`：只能包含稳定数据结构、Protocol 和纯访问规则，不能导入厂商 SDK。
- `document`：只负责从原始字节得到可重建 Node 和制品，不负责在线召回。
- `indexing`：只负责 Embedding 和向量索引读写，不决定发布事务。
- `search`：只负责查询侧候选、排序和上下文前的授权编排，不生成回答。
- `answering`：只消费 Packed Context 并验证回答，不读取向量库。
- `operations`：审计、SSE 和运行状态；故障不得改变核心回答语义。

## 迁移步骤

1. **冻结契约**：运行全量测试、P0 API/端口/索引检查，保留旧门面签名。
2. **建立分层入口**：新增六个功能包；新业务代码只从包入口导入。
3. **消除重复**：删除 `KnowledgeService` 内的解析/分块副本，旧私有名称改为同一实现的
   对象别名。
4. **抽取检索编排**：把授权下推、Dense/BM25、RRF、精排、Context 和审计迁入
   `search.pipeline`，`ChatService` 只组装运行配置并委托。
5. **分离模型无关契约**：把 LLM 错误、结果和熔断器迁入 `answering.llm_types`，
   `services.llm_service` 继续兼容导出。
6. **逐文件反转兼容方向**：把平铺模块实现逐个移入对应子目录，再让旧文件只做
   re-export。每次只迁一阶段并运行阶段测试，避免大爆炸式改名。
7. **移除兼容层**：至少经过两个发布周期、确认没有外部旧导入后再删除平铺文件；删除
   前加入弃用日志和导入扫描。

当前已完成步骤 1～6。真实实现已按 loading、splitting、indexing、retrieval、
answering 顺序迁移；旧平铺文件不再定义业务类或函数，只通过 `_compat.reexport` 暴露
同一个对象。`llamaindex_indexing.py` 使用模块级别名，继续兼容既有故障注入路径。

步骤 7 尚未执行：旧文件仍服务于外部脚本和历史扩展，必须经过发布观察期后删除。

## 兼容性规则

- `services.chat_service.ChatService`、`services.knowledge_service.KnowledgeService`、
  `services.llm_service.LLMService` 的公开签名不得改变。
- `services.rag.contracts` 等旧路径在兼容期仍返回同一个类/函数对象。
- 不修改 Document/Node ID 算法、Manifest Schema、Collection 名称或 active pointer。
- 兼容别名必须使用对象别名，禁止复制实现。
- 新目录中的应用模块继续受 `layers.py` 的厂商 SDK 禁止导入检查约束。
- `python scripts/check_rag_legacy_imports.py` 必须保持通过，防止生产代码重新依赖旧路径。

## 验证命令

```bash
python -m pytest -q
python scripts/check_rag_p0_contract.py --check-index
python scripts/evaluate_rag_context.py
python scripts/evaluate_rag_grounding.py
python scripts/check_rag_legacy_imports.py
```
