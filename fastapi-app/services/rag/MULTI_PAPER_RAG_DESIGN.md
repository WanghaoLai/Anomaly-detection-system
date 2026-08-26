# 多论文知识库 RAG 改进设计方案

> 文档状态：设计提案<br>
> 适用范围：`fastapi-app/services/rag/`、`KnowledgeService`、`ChatService` 及相关配置、评测和运维流程<br>
> 目标场景：构建多篇科研论文知识库，支持跨论文检索、比较、归纳、证据引用与可信回答<br>
> 约束：本方案保持现有 API、领域端口、权限边界和蓝绿发布思想，不要求一次性切换全部组件

## 1. 方案摘要

项目 RAG 的目标架构选择为：

> **结构感知的分层混合 RAG（Hierarchical Hybrid RAG）+ 条件式多查询分解 + 跨论文多样性控制 + 多语言 Cross-Encoder 重排序 + 证据矩阵融合 + Claim 级可信生成。**

完整工作流如下：

```text
原始论文/普通文档
  -> 格式识别与解析路由
  -> Docling 版面解析
  -> GROBID 学术元数据补充
  -> MarkItDown 通用格式回退
  -> 统一 PaperDocument / DocStore
  -> 父子分块、结构关系与稳定 ID
  -> 论文级表示 + Chunk 级表示
  -> text-embedding-v4 Dense 向量 + Sparse/BM25
  -> Chroma 近期索引 / Qdrant 目标索引

用户问题 + 对话历史
  -> 查询类型判断
  -> 规则直通 / 独立问题改写 / 条件式问题分解
  -> ACL 与论文属性过滤
  -> 论文级粗筛
  -> 子查询 Dense + Sparse/BM25 Chunk 召回
  -> RRF 融合
  -> Document-aware MMR / 论文配额控制
  -> 多语言 Cross-Encoder 精排
  -> 父章节、邻接节点、表格/图注按需扩展
  -> 证据矩阵 + 查询感知上下文装箱
  -> 综合型 Prompt + Claim 级引用生成
  -> 确定性引用/原子校验 + 可选 NLI 语义支持校验
  -> 回答、拒答或补检
```

核心选型结论：

| 能力 | 选型 |
| --- | --- |
| 论文解析 | Docling 为主，GROBID 补充学术元数据，MarkItDown 作为通用格式和故障回退 |
| 文档事实源 | 保留内容寻址原文件和统一 DocStore，升级为结构化论文 Document |
| 分块 | 父子分块、章节感知、表格/公式/图注原子保护 |
| 索引 | 论文级索引 + Chunk 级索引，支持标题、摘要等命名表示 |
| Embedding | `text-embedding-v4`，优先评测 1024 维，继续通过现有 DashScope 端口接入 |
| 向量库 | 近期继续 Chroma；规模化、混合检索目标态迁移 Qdrant |
| 查询处理 | 规则直通 + LLM 独立问题改写 + 条件式多查询分解 |
| 召回 | Dense + Sparse/BM25，使用 RRF 做无量纲融合 |
| 跨论文控制 | Document-aware MMR、单篇软配额和跨论文覆盖约束 |
| 重排序 | 默认启用多语言 Cross-Encoder，失败时回退 RRF |
| 上下文 | 证据矩阵、父子上下文恢复、查询感知硬预算装箱 |
| 生成 | 面向比较与综合的 Prompt，Claim 级来源引用 |
| Grounding | 保留确定性门禁，逐步加入独立 NLI/语义蕴含验证 |
| 配置 | 环境变量控制运行参数，Profile 组织场景配置，Manifest 固化不可变索引契约 |

## 2. 当前架构基线

### 2.1 可直接复用的设计

当前 RAG 已完成清晰的分层重构：

```text
core       领域对象、端口、ACL
document   加载、清洗、切分、解析、制品存储
indexing   Embedding、向量库适配和蓝绿写入
search     Dense/BM25、RRF、精排和授权检索编排
answering  查询补全、Context、Prompt、Grounding
operations 审计和 SSE 生命周期
```

以下能力应继续保留：

1. `DocumentLoader`、`DocumentPreprocessor`、`NodeParser`、`EmbeddingModel`、`VectorStore`、`VectorIndexWriter` 等稳定 Protocol。
2. API 只依赖 `ChatService`、`KnowledgeService`、`LLMService` 兼容门面。
3. 原始文件和统一 DocStore 是事实源，Embedding 和向量库是可重建派生物。
4. 稳定 Document/Node ID、内容哈希和来源位置 metadata。
5. 全量影子索引、Manifest 校验、CAS 发布和失败补偿。
6. `document/query` Embedding 输入类型区分、归一化和维度一致性检查。
7. ACL 在召回前下推，并在服务端再次过滤。
8. Dense + BM25 + RRF、可选 Cross-Encoder、严格 Context Token 预算。
9. Claim 级引用、原子值校验、服务端渲染和证据不足拒答。
10. 检索 release、候选、引用、Token、耗时和模型版本审计。

### 2.2 面向多论文场景的主要缺口

| 当前能力 | 多论文场景缺口 | 影响 |
| --- | --- | --- |
| MarkItDown 统一转 Markdown | 对双栏、复杂表格、公式、脚注、图注和扫描论文的结构恢复有限 | 段落顺序错误、实验条件与结果分离、引用位置不准 |
| 单层 Markdown Chunk | 没有论文、章节、段落之间的显式父子关系 | 精确块命中后难以恢复充分语境 |
| 单一 Chunk 索引 | 无论文级路由或摘要级粗筛 | 相似论文较多时召回噪声和成本上升 |
| `text-embedding-v2` | 中英文跨语言、长论文语义和领域指令能力存在升级空间 | 语义召回上限受限 |
| 进程内 BM25 release cache | 多进程重复占用内存，规模扩大后重建和一致性成本增加 | 扩容和发布复杂度上升 |
| 短追问拼接历史 | 不是完整的独立问题改写，也不能拆解跨论文综合问题 | 召回意图不清、无关历史污染 |
| 全局 Top-K + RRF | 缺少论文级去偏和覆盖约束 | 单篇论文可能占满最终上下文 |
| Cross-Encoder 默认关闭 | 融合后缺少查询—证据深层相关性判断 | 对相似术语、反例和限定条件区分不足 |
| 结果只做平铺装箱 | 没有按“子问题 × 论文”组织证据 | 生成模型容易遗漏对比维度或偏向第一篇论文 |
| 词面 Grounding | 对同义表达、缩写、跨语言证据偏保守 | 可信但可能误删有依据的 Claim |

## 3. 设计原则

### 3.1 事实源优先

- 原始文件、解析产物、结构化 Document、Node 和 Release Manifest 是可审计事实。
- 论文摘要、Embedding、Sparse 向量和向量索引均为派生物，必须可从 DocStore 重建。
- 不允许从旧向量库反向拼接正文生成新的论文事实源。

### 3.2 结构优先于固定长度

- 章节、段落、表格、公式、图注、脚注和参考文献是论文的真实语义边界。
- Token 长度用于控制成本，不能破坏表格行、公式及限定条件的完整性。
- 子块负责定位证据，父块负责恢复语境；不能用超大 Chunk 同时承担二者。

### 3.3 检索与生成解耦

- 查询改写模型只能产生检索计划，不能产生最终事实。
- Reranker 只能改变候选顺序，不能绕过 ACL 或创造证据。
- 生成模型只消费已授权、已装箱且带稳定引用的证据。
- Grounding Validator 决定内容能否发布，生成模型不拥有发布权。

### 3.4 渐进式替换

- 首先升级解析、数据模型和检索策略，再决定向量数据库迁移。
- Chroma 和 Qdrant 适配器在同一端口后并存，通过 Profile 和 Release 选择。
- Embedding、Sparse 检索、Reranker、NLI 可以独立灰度，不绑定为一次发布。

### 3.5 评测驱动

- 所有模型、阈值、K 值、维度、分块策略和数据库迁移都必须经过固定评测集。
- 不能以厂商通用榜单代替本项目的中英文工业异常检测论文评测。
- 每次 Release 必须能够关联解析器、模型、检索 Profile、Prompt 和评测报告。

## 4. 目标逻辑架构

```text
┌────────────────────────── 文档侧 ──────────────────────────┐
│ Upload API                                                   │
│   -> ParserRouter                                            │
│      ├─ DoclingPaperLoader                                   │
│      ├─ GrobidMetadataEnricher                               │
│      └─ MarkItDownDocumentLoader                             │
│   -> PaperDocumentNormalizer                                 │
│   -> HierarchicalPaperNodeParser                             │
│   -> ContentAddressedFileStore + PaperDocumentStore          │
│   -> PaperRepresentationBuilder                              │
│   -> EmbeddingModel + SparseEncoder                          │
│   -> MultiGranularityIndexWriter                             │
│   -> Shadow Release Validation -> Atomic Publish             │
└──────────────────────────────────────────────────────────────┘

┌────────────────────────── 查询侧 ──────────────────────────┐
│ User Query + Trusted Principal + History                     │
│   -> QueryIntentRouter                                       │
│   -> StandaloneQueryRewriter                                 │
│   -> ConditionalQueryDecomposer                              │
│   -> AuthorizedPaperRetriever                                │
│   -> AuthorizedChunkHybridRetriever                          │
│   -> RRF Fusion                                              │
│   -> DocumentAwareDiversifier                                │
│   -> CrossEncoderReranker                                    │
│   -> RelatedEvidenceExpander                                 │
│   -> EvidenceMatrixBuilder                                   │
│   -> ContextPacker                                           │
│   -> GroundedSynthesisPrompt                                 │
│   -> LLM Structured Claims                                   │
│   -> Deterministic Validator + Optional NLI Validator        │
│   -> Server-side Rendering / Refusal                         │
└──────────────────────────────────────────────────────────────┘
```

目标依赖方向仍遵守现有架构：

```text
API
  -> Service Facades
  -> document / indexing / search / answering
  -> core Protocols
  -> provider adapters and operations
```

Docling、GROBID、DashScope、Chroma、Qdrant 和具体 Reranker SDK 只能出现在适配器或装配层，不能进入 `core`、纯切分算法、融合算法和 Grounding 规则。

## 5. 文档解析与入库设计

### 5.1 解析路由

新增逻辑角色 `ParserRouter`，根据文件类型和解析能力选择路径：

| 输入 | 主路径 | 补充/回退 |
| --- | --- | --- |
| 可提取文本的学术 PDF | Docling | GROBID 补充元数据；Docling 失败时 MarkItDown |
| 扫描 PDF | Docling + OCR | 低置信度时标记人工复核，不静默回退为错误文本 |
| DOCX/PPTX/XLSX/HTML/Markdown 等 | MarkItDown | 沿用现有格式清洗 |
| PDF 学术头信息和参考文献 | GROBID | 失败不阻断正文入库，但写入诊断 |

解析优先级不是“任一解析器成功即完成”，而是：

1. Docling 负责页面版面、阅读顺序、正文结构、表格、公式、图像和图注。
2. GROBID 负责标题、作者、机构、摘要、关键词、章节、参考文献、DOI 等学术字段。
3. 使用页码、标题文本、段落文本或坐标将两套结果对齐。
4. 对冲突字段记录来源和置信度；标题、DOI 等标识字段不能无依据覆盖。
5. MarkItDown 继续处理非论文格式，也作为 Docling 服务不可用时的受控回退。

### 5.2 解析诊断

每篇论文保存以下诊断，不合格时阻止自动发布或标记降级：

```json
{
  "parser_profile": "paper_pdf_v1",
  "primary_parser": "docling",
  "metadata_enricher": "grobid",
  "fallback_used": false,
  "page_count": 12,
  "ocr_page_count": 0,
  "reading_order_confidence": 0.96,
  "title_detected": true,
  "abstract_detected": true,
  "section_count": 8,
  "table_count": 5,
  "figure_count": 6,
  "formula_count": 21,
  "reference_count": 43,
  "unresolved_blocks": 0,
  "warnings": []
}
```

建议将以下情况视为发布阻断：

- 转换后正文为空或文本量与页数严重不匹配。
- 大量页面只有页眉页脚或乱码。
- 稳定 ID 冲突且正文或位置不一致。
- 表格/公式块被拆成无法追溯的片段。
- 原始文件哈希、DocStore 文本哈希不一致。

扫描件、GROBID 暂时不可用、个别图像未解析等情况可作为显式降级，不应伪造缺失内容。

## 6. 结构化论文 Document 模型

### 6.1 模型分层

保留现有 `Document` 和 `Node` 领域对象的稳定入口，结构化论文数据通过 schema 和 metadata 扩展，不把 Docling/GROBID 类型暴露给业务层。

建议将 DocStore 升级为以下逻辑结构：

```text
PaperDocument
├── identity
│   ├── document_id
│   ├── source_sha256
│   ├── doi / arxiv_id / external_ids
│   └── document_schema_version
├── bibliographic_metadata
│   ├── title
│   ├── authors
│   ├── affiliations
│   ├── publication_year
│   ├── venue
│   ├── language
│   ├── keywords
│   └── abstract
├── source
│   ├── filename / storage_key
│   ├── media_type / byte_size
│   └── uploaded_at
├── structure
│   ├── sections
│   ├── paragraphs
│   ├── tables
│   ├── figures
│   ├── formulas
│   ├── footnotes
│   └── references
├── relations
│   ├── section_parent
│   ├── previous / next
│   ├── caption_of
│   ├── refers_to
│   └── cites
├── normalized_markdown
├── nodes
└── diagnostics
```

### 6.2 标识策略

建议将 ID 区分为三个层级：

- `document_id`：由规范化来源身份、原文件 SHA256 和 ingestion schema 生成。
- `parent_node_id`：由文档、节点类型、章节路径、页码/坐标和父块正文哈希生成。
- `child_node_id`：由 parser schema、document、parent、字符/块位置和正文哈希生成。

DOI、标题或文件名不能单独作为 `document_id`：同一论文可能存在预印本、出版版、修订版和不同 PDF 文件。可以额外建立 `work_id`，将多个版本归入同一学术作品，但索引和引用仍必须指向具体文档版本。

### 6.3 Node metadata

Chunk 索引至少应保存：

| 类别 | 字段 |
| --- | --- |
| 身份 | `node_id`、`document_id`、`work_id`、`parent_node_id`、`node_level`、`node_type` |
| 论文 | `paper_title`、`authors`、`publication_year`、`venue`、`doi`、`language` |
| 结构 | `section_path`、`section_type`、`chunk_index`、`page_start/page_end`、`bbox` |
| 关系 | `previous_node_id`、`next_node_id`、`caption_target_id`、`reference_ids` |
| 内容 | `block_types`、`token_count`、`protected`、`oversized_protected` |
| 来源 | `source_filename`、`source_sha256`、`source_uri`、`citation_label` |
| 权限 | `visibility`、`allowed_roles`、`allowed_user_ids` |
| 版本 | `parser_schema_version`、`embedding_schema_version`、`release_id` |

向量库只保存可过滤的标量或受支持数组字段；完整 bbox、作者对象、解析树和参考文献对象保留在 DocStore。

## 7. 父子分块与结构关系

### 7.1 节点类型

建议至少定义以下逻辑节点：

| 节点类型 | 用途 | 是否默认向量化 |
| --- | --- | --- |
| `paper_summary` | 标题、摘要、关键词、主要章节摘要，用于论文级粗筛 | 是 |
| `section_parent` | 完整章节或子章节上下文 | 可选，优先存储而非直接粗召回 |
| `text_chunk` | 普通段落组成的证据块 | 是 |
| `table_chunk` | 表题、表头、行数据和脚注 | 是 |
| `figure_caption` | 图题、图注及邻近解释 | 是 |
| `formula_chunk` | 公式、编号和紧邻定义/解释 | 是 |
| `reference_entry` | 参考文献条目 | 默认不进入正文问答索引，可进入引用图旁路 |

### 7.2 分块参数基线

建议以项目现有 500/50 Token 为起点，建立论文 Profile：

```text
child chunk max       500 tokens
child target          350–420 tokens
child minimum          80 tokens
child overlap          40–60 tokens，且只复制完整语义单元
section parent       1000–2000 tokens 或按完整子章节保存
table/formula         原子优先，超限时按结构行或逻辑区域拆分
```

具体规则：

1. 不跨章节合并普通段落。
2. 标题路径以受控前缀加入 Embedding 文本，例如“论文标题 > 方法 > 损失函数”。
3. 表格按“表题 + 表头 + 若干完整行 + 脚注”切分，各子块重复必要表头。
4. 公式与变量定义、公式编号和紧随其后的解释保持关联。
5. 图注与正文引用形成 `caption_of/refers_to` 关系，不对图片内容进行无依据文本补全。
6. 父节点正文不直接占用最终上下文；只有子块命中后按需要提取父节点相关单元。
7. Node ID 不依赖 Embedding 结果、运行时间或随机数。

### 7.3 上下文恢复策略

邻接扩展只能发生在精排之后：

- 若命中块以代词、编号或承接句开头，补前一个同章节节点。
- 若命中结果表但缺少实验设置，补最近的实验设置父段或表头。
- 若命中公式解释，补对应公式块；若命中图表引用，补图注或表注。
- 默认最多为一个命中块补充 1 个父节点片段和前后各 1 个邻接节点。
- 扩展内容仍受 ACL、去重、总预算和引用映射约束。

## 8. 多粒度索引设计

### 8.1 近期 Chroma 结构

近期继续 Chroma 时，建议每个 release 构建两个逻辑索引：

```text
knowledge_papers_shadow_<release_id>   论文级表示
knowledge_chunks_shadow_<release_id>   证据 Chunk 表示
```

若需最大限度兼容现有单 collection 代码，可先将两类节点写入同一 collection，并使用 `node_level=paper/chunk` 做查询前过滤；但长期应使用独立逻辑索引，避免不同粒度互相竞争 Top-K。

Manifest 从单 collection 字段演进为多索引描述，同时保留兼容字段：

```json
{
  "collection_name": "knowledge_chunks_shadow_<release_id>",
  "indexes": {
    "papers": {
      "provider": "chroma",
      "collection": "knowledge_papers_shadow_<release_id>",
      "node_count": 120
    },
    "chunks": {
      "provider": "chroma",
      "collection": "knowledge_chunks_shadow_<release_id>",
      "node_count": 18420
    }
  }
}
```

发布时必须对两个索引和 DocStore 同时验收，再通过单一 release 指针原子切换。不能先发布论文索引、再发布 Chunk 索引。

### 8.2 Qdrant 目标结构

Qdrant 目标态建议每个 Chunk point 使用命名向量：

```text
vectors:
  dense          text-embedding-v4 Dense
  title_dense    可选的标题/章节表示
sparse_vectors:
  lexical        text-embedding-v4 Sparse 或独立 Sparse Encoder
payload:
  document_id / work_id / parent_node_id
  paper_title / year / venue / language
  section_path / node_type / page
  ACL / release_id / schema versions
```

论文级 point 可放在独立 collection，或使用 `node_level=paper` 和命名向量隔离。选择原则：

- 论文和 Chunk 更新、索引参数或保留周期不同：使用独立 collection。
- 规模较小且希望单次 payload 管理：可用同 collection + 类型过滤。
- ACL、`release_id`、`document_id`、`publication_year`、`node_type` 等高频过滤字段必须建立 payload index。

Qdrant 迁移后，Dense、Sparse、过滤和 RRF 可以在服务端统一执行；应用层仍保留融合端口，以便 A/B、回退和审计。

## 9. Embedding 与 Sparse 表示

### 9.1 Dense Embedding

目标模型选择 `text-embedding-v4`，优先评测 1024 维，理由：

- 适配中英文及跨语言论文检索。
- 支持自定义维度，1024 维适合作为效果与成本基线。
- 可对 query 设置论文检索 instruct。
- 现有 DashScope 适配器已经区分 `document/query`，并具备批处理、重试、归一化和维度校验。

建议文档输入模板保持确定性：

```text
论文：{paper_title}
章节：{section_path}
类型：{node_type}

{chunk_text}
```

论文级表示可使用：

```text
标题 + 关键词 + 摘要 + 主要章节标题
```

查询侧 instruct 应固定在 Profile 和 Manifest 中，例如“检索与用户研究问题相关的科研论文证据”，不能由最终用户任意注入。

### 9.2 Sparse/BM25

按阶段选择：

1. **Chroma 阶段**：继续使用 release 级 BM25，升级分词字段，将论文标题、章节名、数据集、算法名和正文共同纳入索引。
2. **Qdrant 阶段**：优先评测 `text-embedding-v4` Sparse 输出或独立多语言 Sparse Encoder，并使用 Qdrant sparse vector。
3. 术语、英文缩写、模型版本、指标名、数据集名、公式符号、DOI 和数值必须保留精确 Token。

不直接比较 Dense 和 Sparse 原始分数，统一使用 RRF 或经过离线标定的融合策略。

### 9.3 模型迁移规则

- v2 与 v4 向量不得写入同一向量字段或 collection。
- 维度、归一化、输入模板或 instruct 变化均视为 Embedding schema 变化。
- 通过现有影子构建生成 v4 候选 release。
- 同一评测请求可双路查询 v2/v4，但只允许一个 release 向用户提供正式答案。
- 达到质量、延迟、成本和稳定性门槛后再原子切换。

## 10. 查询理解与改写

### 10.1 查询类型

`QueryIntentRouter` 输出以下类型之一：

| 类型 | 示例 | 处理方式 |
| --- | --- | --- |
| `direct` | “TranAD 在 SWaT 上使用什么指标？” | 原问题直通 |
| `follow_up` | “它在 WADI 上呢？” | 结合有限历史改写为独立问题 |
| `comparison` | “比较三篇论文的异常评分方法” | 改写并拆成多个检索子问题 |
| `synthesis` | “这些论文对 Transformer 优势有什么共同结论？” | 论文级路由 + 条件式分解 |
| `lookup` | “论文 DOI 是什么？” | 强化标题、作者、DOI 等字段检索 |
| `citation_graph` | “哪些论文引用了某方法？” | 进入可选引用图旁路，不走普通正文生成 |

### 10.2 规则直通

满足以下条件时不调用改写模型：

- 问题语义完整，无明显指代。
- 未引用历史中的“它、该方法、上述结果”等实体。
- 不是比较、归纳、演进、异同或多条件问题。
- 长度和复杂度在单次检索可处理范围内。

这样可降低延迟并避免 LLM 改写引入术语漂移。

### 10.3 独立问题改写

仅对追问调用轻量 LLM，输出结构化结果：

```json
{
  "intent": "follow_up",
  "standalone_query": "TranAD 在 WADI 数据集上报告了哪些评价指标和结果？",
  "preserved_terms": ["TranAD", "WADI"],
  "filters": {},
  "confidence": 0.94
}
```

约束：

- 只使用最近有限轮对话。
- 算法名、数据集名、指标、数值和论文标题必须原样保留。
- 不允许改写模型补充对话中不存在的论文、年份或结论。
- 低置信度时使用原查询与改写查询双路召回，不能静默替换。

### 10.4 条件式查询分解

只对比较或综合型问题生成 2–5 个子查询：

```json
{
  "intent": "comparison",
  "standalone_query": "比较 TranAD、Anomaly Transformer 和 OmniAnomaly 的异常评分机制与实验结论",
  "subqueries": [
    {"id": "Q1", "aspect": "method", "query": "TranAD Anomaly Transformer OmniAnomaly 异常评分机制"},
    {"id": "Q2", "aspect": "datasets", "query": "三种方法使用的数据集和实验设置"},
    {"id": "Q3", "aspect": "results", "query": "三种方法的主要实验结果和限制"}
  ],
  "filters": {}
}
```

限制：

- 不默认启用 HyDE，避免生成的假设答案污染科研证据召回。
- 子查询必须覆盖原问题的不同方面，而不是同义改写堆叠。
- 每个子查询保留来源 ID，供后续证据矩阵和审计使用。
- 总查询数设硬上限，超限时优先保留用户明确要求的比较维度。

## 11. 分层混合检索流程

### 11.1 第一级：论文级粗筛

输入为独立问题或子查询集合，检索论文级表示：

1. 按 ACL、语言、年份、作者、数据集等显式条件过滤。
2. Dense 检索标题、摘要和关键词表示。
3. Sparse/BM25 检索论文标题、算法名、数据集、作者和 DOI。
4. RRF 合并，得到 10–20 篇候选论文。
5. 用户明确指定论文时，将其作为过滤条件或高优先候选，而不是仅做普通关键词。

论文级粗筛是减少 Chunk 搜索空间和保证多论文覆盖的路由层，不直接为最终 Claim 提供正文证据；最终引用必须落到具体 Chunk、表格、图注或元数据记录。

### 11.2 第二级：Chunk 级召回

对每个子查询在授权候选论文中执行：

```text
Dense Top 40–60
+ Sparse/BM25 Top 40–60
-> RRF
-> 每个子查询候选上限 60–100
```

需要保留以下审计信息：

- `query_id`、`subquery_id` 和 `aspect`。
- 论文级排名和 Chunk 级排名。
- Dense/Sparse 原始分数、rank 和 RRF 分数。
- ACL/metadata 过滤条件。
- release、Embedding、Sparse 和检索 Profile 版本。

### 11.3 RRF 融合

继续使用 RRF 作为默认融合，原因是不同检索通道的原始分数不可直接比较。融合对象从当前两个列表扩展为：

```text
subquery × {dense, sparse}
```

同一 Node 被多个子查询命中时可累积融合信号，但需要记录来自哪些子查询，防止审计时丢失解释。

### 11.4 跨论文去偏与多样性控制

RRF 后增加 `DocumentAwareDiversifier`：

1. 先按相关性选择最高候选。
2. 对与已选 Chunk 高度相似的内容施加冗余惩罚。
3. 对同一 `document_id` 连续占位施加软配额。
4. 对尚未覆盖但有高相关候选的子查询/论文给予覆盖增益。
5. 不强制引入低相关论文；相关性下限始终优先于覆盖数。

建议初始策略：

- 粗排阶段单篇论文最多 5–8 个 Chunk。
- 比较/综合问题目标覆盖 3–5 篇论文。
- 事实查找问题不强制多论文，允许单篇证据。
- 相同表格的不同行、父子重复块和高度重叠 Chunk 只保留互补证据。

可用以下概念分数表达，但具体权重必须通过评测确定：

```text
selection_score
  = relevance
  - lambda_redundancy * content_similarity
  - lambda_doc * same_document_saturation
  + lambda_aspect * uncovered_subquery_gain
  + lambda_doc_coverage * uncovered_document_gain
```

## 12. Cross-Encoder 重排序

### 12.1 默认策略

多论文 Profile 默认启用多语言 Cross-Encoder。候选模型优先评测：

- `Qwen3-Reranker-0.6B`：首选本地基线，兼顾中英文、效果和资源。
- 同等级已审核的多语言 Cross-Encoder：作为 A/B 对照。
- 资源充足时再评测更大模型，不直接以参数规模决定上线。

输入不仅包含正文，还应包含最少必要结构：

```text
query: 独立问题或对应子查询
document:
  论文标题
  章节路径
  节点类型
  Chunk 正文
```

### 12.2 批次和回退

- 对融合后的 50–100 个候选分批重排。
- 精排后保留 12–20 个证据候选，再做邻接扩展和 Context Packing。
- 设置独立超时、批大小和总 deadline。
- 模型加载失败、超时或资源不足时回退到 Document-aware RRF 顺序。
- 回退必须写审计状态，不能伪装为 Cross-Encoder 成功。

### 12.3 重排粒度

对分解查询，应综合主问题和子查询相关性：

- 子查询用于判断该证据是否回答具体方面。
- 主问题用于避免局部相关但偏离整体任务。
- 重排输出同时记录 `primary_score` 和 `aspect_score`，最终策略由 Profile 固化。

## 13. 关联证据扩展

`RelatedEvidenceExpander` 在精排后读取 DocStore 关系，不直接扩大向量 Top-K：

| 命中类型 | 可扩展内容 |
| --- | --- |
| 普通段落 | 同章节父节点中的相关句、前后节点 |
| 表格行 | 表题、表头、脚注、实验设置段 |
| 公式 | 公式定义、变量解释、紧邻推导 |
| 图注 | 对应图和正文中的引用说明 |
| 结论 | 支持该结论的结果段或限制段 |

扩展规则：

- 所有扩展节点必须与命中节点属于同一授权论文版本。
- 扩展内容单独保留来源位置，可以与主节点共享证据组，但不能伪造成主节点正文。
- 扩展只补充分辨结论所需的上下文，不复制整个章节。
- 扩展后再次做 Node、正文、父子内容和跨论文重复检测。

## 14. 证据矩阵与上下文装箱

### 14.1 证据矩阵

在 `ContextPacker` 前增加 `EvidenceMatrixBuilder`，将平铺候选组织为：

```text
                 Paper A       Paper B       Paper C
方法机制          K1, K2        K5             K8
实验数据集        K3            K6             K9
主要结果          K4            K7             K10
限制/反例         -             K11            K12
```

矩阵不直接暴露给用户，也不改变证据内容；它用于：

- 判断每个子查询是否已有证据。
- 控制多论文覆盖和 Token 分配。
- 识别结论冲突、证据空缺和只由单篇支持的观点。
- 为综合型 Prompt 提供显式组织结构。

### 14.2 装箱策略

保留现有 `ContextPacker` 的以下行为：

- 总 Token 硬预算。
- 来源、论文、章节、页码、Node ID 与引用编号计入预算。
- 原子命令、公式、数值和表格结构不做危险截断。
- Node、正文和信息单元三级去重。
- 引用编号在最终去重和预算过滤后生成。

多论文 Profile 增加：

1. 先为每个高优先子查询保留最小证据预算。
2. 再为不同论文分配覆盖预算。
3. 剩余预算按精排分数竞争。
4. 同一论文、同一章节和同一证据组设软上限。
5. 父节点只摘取与查询相关的完整信息单元。

建议首版上下文预算从现有 2800 Token 评测扩展到 5000–8000 Token；上线值由所选生成模型上下文、延迟和答案完整度共同决定，不能只追求更大。

### 14.3 引用显示

建议引用标签包含：

```text
[K3] 论文标题 · Results > SWaT · p.8 · Node <id>
```

回答正文仍使用简洁 `[K3]`，来源列表显示论文标题、作者/年份、章节、页码和短摘录。DOI 或外部链接只有在 DocStore 中已验证存在时才展示。

## 15. 综合生成与 Grounding

### 15.1 生成模式

在现有 `general/knowledge_base` 之上，知识库内部增加回答策略，不必暴露为新的用户模式：

- `fact_lookup`：定位单篇或少量论文中的明确事实。
- `comparison`：按统一维度比较多篇论文。
- `synthesis`：总结共识、差异、冲突和研究空缺。
- `metadata_lookup`：回答标题、作者、年份、DOI、数据集等结构字段。

### 15.2 结构化输出

生成模型提交的候选结构扩展为：

```json
{
  "mode": "knowledge_base",
  "answer_type": "comparison",
  "refusal": false,
  "claims": [
    {
      "text": "论文 A 使用重构误差构造异常分数。",
      "citations": ["K1"],
      "aspect": "method",
      "support": "direct"
    }
  ],
  "agreements": ["..."],
  "differences": ["..."],
  "evidence_gaps": ["论文 C 未报告该数据集上的结果"]
}
```

服务端仍只发布通过校验的 Claim；`agreements/differences` 若包含新事实，也必须转换为带引用 Claim 后才可发布。

### 15.3 Prompt 约束

综合型 Prompt 应明确要求：

1. 只依据 `<knowledge_context>`。
2. 区分论文明确报告的事实、模型做出的跨论文归纳和证据缺失。
3. 比较时使用一致维度，不能把不同数据集或评价协议的数值直接判定为优劣。
4. 每个 Claim 只表达一个可验证结论并附真实 K 引用。
5. “多篇论文一致认为”必须至少有两篇独立论文证据。
6. 冲突结论分别引用，不强行消解。
7. 论文没有报告某项内容时表述为“当前证据未找到”，不能推断为“不存在”。

### 15.4 确定性校验

继续保留：

- JSON Schema、模式、拒答和 Claim 数量检查。
- 引用必须属于本次 `citation_map`。
- 数字、单位、公式、路径、URL、模型名和数据集名原子匹配。
- ACL 与 release 一致性。
- 无证据 Claim 丢弃，全部失败时安全拒答。

新增跨论文规则：

- 声称“共同、均、普遍、一致”时至少覆盖两篇不同 `document_id/work_id`。
- 声称“优于、最高、最好”时必须验证比较对象、数据集、指标和评价协议一致。
- 单篇论文的自述不能被表述为跨论文共识。
- 引用预印本和出版版的同一作品时，默认按一个 `work_id` 计算独立来源数。

### 15.5 NLI/语义支持校验

NLI 作为第二层验证，不能替代确定性门禁：

```text
Claim
  -> 引用存在与 ACL 校验
  -> 数值/术语/公式原子校验
  -> 词面支持校验
  -> 可选 NLI entailment / contradiction
  -> 发布或丢弃
```

NLI 只接收 Claim 与其引用证据，不接收未授权全文。输出至少包含：

- `entailment_score`
- `contradiction_score`
- `model_version`
- `fallback_reason`

模型超时或不可用时，默认回退到现有确定性校验；是否允许回退后的 Claim 发布由 Profile 固化。涉及关键数值、操作命令或权限信息时继续使用 fail-closed。

## 16. 权限、安全与提示注入

### 16.1 ACL

- 论文级粗筛和 Chunk 级召回都必须在 Top-K 前下推 ACL。
- 服务端在融合、扩展和装箱前再次过滤。
- 父节点、邻接节点、图表和参考文献扩展继承具体文档 release 的 ACL。
- `work_id` 关联不能让用户通过公开版本读取受限版本内容。
- Qdrant 迁移后对 ACL 高频字段建立 payload index，应用层二次过滤仍保留。

### 16.2 提示注入

- 论文正文、脚注、附录和元数据全部视为不可信数据。
- 解析器不得执行论文中嵌入的 URL、脚本、宏或外部资源请求。
- 查询改写模型和生成模型使用不同 Prompt 与输出契约。
- 文档中的“忽略以上规则”等文本只能作为被检索材料，不能进入系统指令层。
- 审计日志避免保存未经脱敏的完整问题和受限正文。

## 17. 配置与 Profile 设计

### 17.1 配置层级

```text
代码默认值
  <- 环境变量覆盖
  <- 场景 Profile 选择
  <- Release Manifest 固化不可变索引参数
  <- 请求级只读选择（禁止覆盖安全边界）
```

环境变量控制部署和运行参数；Profile 将相关参数组织成可评测方案；Manifest 固化实际构建索引时使用的值。

### 17.2 建议 Profile

```text
legacy_general_v1       现有普通知识库兼容流程
paper_chroma_v1         Chroma + 论文父子分块 + 本地 BM25
paper_chroma_v4_eval    v4 影子索引和双路评测
paper_qdrant_v1         Qdrant Dense/Sparse 多粒度目标态
```

### 17.3 建议新增配置

以下名称为设计建议，最终实现应遵循项目现有 `AI_RAG_*` 风格：

| 配置 | 建议默认值 | 含义 |
| --- | ---: | --- |
| `AI_RAG_PROFILE` | `legacy_general_v1` | 当前运行 Profile |
| `AI_RAG_PAPER_PARSER` | `docling` | 论文主解析器 |
| `AI_RAG_GROBID_ENABLED` | `true` | 是否补充学术元数据 |
| `AI_RAG_OCR_ENABLED` | `true` | 是否允许扫描页 OCR |
| `AI_RAG_PARENT_TOKENS` | `1600` | 父章节目标预算 |
| `AI_RAG_CHILD_TOKENS` | `500` | 子块上限 |
| `AI_RAG_CHILD_OVERLAP_TOKENS` | `50` | 子块完整语义单元重叠 |
| `AI_RAG_EMBEDDING_DIMENSION` | `1024` | v4 向量维度 |
| `AI_RAG_EMBEDDING_INSTRUCT` | 固定受控文本 | 查询检索指令 |
| `AI_RAG_PAPER_CANDIDATE_K` | `15` | 论文级候选数 |
| `AI_RAG_SUBQUERY_LIMIT` | `4` | 查询分解上限 |
| `AI_RAG_DENSE_CANDIDATE_K` | `50` | 每个查询 Dense 候选数 |
| `AI_RAG_SPARSE_CANDIDATE_K` | `50` | 每个查询 Sparse 候选数 |
| `AI_RAG_CANDIDATE_UNION_LIMIT` | `100` | 融合候选上限 |
| `AI_RAG_PER_DOCUMENT_LIMIT` | `6` | 单篇论文候选软上限 |
| `AI_RAG_TARGET_DOCUMENT_COVERAGE` | `4` | 综合问题目标论文数 |
| `AI_RAG_RERANKER_ENABLED` | `true` | 多论文 Profile 默认开启重排 |
| `AI_RAG_RERANK_FINAL_K` | `16` | 精排后证据候选数 |
| `AI_RAG_NEIGHBOR_EXPANSION` | `true` | 是否启用关联证据扩展 |
| `AI_RAG_CONTEXT_TOKENS` | `6000` | 多论文上下文初始评测值 |
| `AI_RAG_NLI_ENABLED` | `false` | NLI 灰度开关 |
| `AI_RAG_VECTOR_PROVIDER` | `chroma` | `chroma/qdrant` |

### 17.4 Manifest 扩展

建议 Manifest 固化：

- 原始文件集、PaperDocument schema、Node 集合哈希。
- Docling/GROBID/MarkItDown 版本和解析 Profile。
- 父子分块参数、ID schema、内容模板版本。
- Embedding provider、model、dimension、normalized、input types、instruct hash。
- Sparse provider、model/tokenizer 和字段模板。
- 论文级/Chunk 级 collection、point 数、向量字段和 payload schema。
- Reranker/NLI/Prompt 不属于索引内容，但其运行版本应进入检索审计和回答审计。

运行配置与 Manifest 不一致时：

- 影响向量解释或索引结构的差异必须阻断检索/发布。
- 仅影响在线 K 值、超时或上下文预算的差异允许通过 Profile 调整，并写入审计。

## 18. 端口与模块演进建议

不改变现有公开门面，内部按以下方向扩展：

```text
core/
  contracts.py
    PaperMetadataEnricher
    HierarchicalNodeParser
    SparseEncoder
    MultiGranularityIndexWriter
    QueryRewriter
    QueryDecomposer
    EvidenceValidator

document/
  routing.py          ParserRouter
  docling_loader.py   Docling 适配器
  grobid.py           GROBID 适配器
  paper_model.py      PaperDocument 映射
  hierarchy.py        父子节点和关系构建

indexing/
  qdrant_store.py     Qdrant 适配器
  sparse.py           Sparse 表示适配
  multi_writer.py     多索引影子构建与验收

search/
  query.py            路由、改写和分解
  paper_retrieval.py  论文级检索
  diversification.py Document-aware MMR/配额
  expansion.py        父子和邻接证据扩展

answering/
  evidence.py         证据矩阵
  synthesis.py        多论文 Prompt/输出契约
  nli.py              可选语义支持适配器
```

模块名称可在实现时调整，但职责边界应保持。特别是：

- `KnowledgeService` 继续负责编排上传、删除、事务、发布和对账。
- `AuthorizedRetrievalPipeline` 演进为多阶段检索编排，但不直接构造厂商客户端。
- `ChatService` 继续做身份和兼容参数适配，不重新吸收检索算法。
- 旧平铺兼容模块保持纯 re-export，直到既定兼容期结束。

## 19. 发布、回滚与一致性

### 19.1 多索引蓝绿发布

```text
DocStore snapshot
  -> paper shadow index
  -> chunk shadow index
  -> sparse/BM25 shadow artifact
  -> 全部数量、ID、维度、ACL 和 schema 对账
  -> 写不可变 Manifest
  -> MySQL 事务更新文档指针
  -> CAS 原子切换 active release
```

任一派生索引失败，整个候选 release 不可发布。旧 release 及其所有索引保留到观察期结束。

### 19.2 回滚

- 在线质量下降：切回旧 active release 和旧 Profile。
- Reranker/NLI 故障：关闭对应运行开关，回退 Document-aware RRF 和确定性验证。
- Qdrant 故障：在双写观察期内切回 Chroma release。
- 解析器升级失败：旧 DocStore schema 和旧索引不变，新解析产物作为独立候选版本丢弃。

### 19.3 对账

健康检查扩展为：

```text
MySQL 文档目录
  == Release Manifest 文档集合
  == DocStore PaperDocument 集合
  == 论文级索引 document_id 集合
  == Chunk 索引 node_id/document_id 集合
  == Sparse 索引 Node 集合
```

并校验父子引用、邻接引用、ACL、Embedding schema 和索引 provider。

## 20. 评测体系与上线门槛

### 20.1 评测集

在现有服务器手册 40 问基线上新增多论文评测集，建议至少包含：

| 类别 | 目标 |
| --- | --- |
| 单论文事实 | 验证精确段落、表格、公式和元数据召回 |
| 跨论文比较 | 验证多篇覆盖、统一比较维度和差异识别 |
| 跨论文综合 | 验证共同结论、矛盾和研究空缺 |
| 多轮追问 | 验证独立问题改写，不污染专有名词 |
| 精确术语 | 模型名、数据集、指标、DOI、年份和公式符号 |
| 跨语言 | 中文提问检索英文论文及中英文混合语料 |
| 表格/图注 | 验证结构解析、关系扩展和数值引用 |
| 负样本 | 库内无答案、证据不足、论文未报告 |
| 权限 | 公开/内部/管理员论文混合检索和旁路攻击 |
| 冲突结论 | 不同论文给出不同结果或限定条件 |

每条评测记录至少保存：

```text
question
intent / subquery expectations
relevant_work_ids / document_ids
relevant_node_ids or evidence anchors
required_aspects
expected_claims
forbidden_claims
access_principal
```

### 20.2 解析指标

- 标题、作者、年份、摘要、章节识别正确率。
- 阅读顺序正确率。
- 表格结构保留率、公式/图注关联率。
- 可回溯页码/位置覆盖率。
- OCR 页面错误率和降级检出率。
- 同版本重建 Document/Node ID 一致率必须为 100%。

### 20.3 检索指标

- Paper Recall@10/20。
- Evidence Recall@K、Hit@K、MRR、nDCG@K。
- 多论文问题的 Document Coverage Recall。
- Aspect Coverage：问题要求的比较维度是否均有证据。
- Duplicate Rate 和单篇论文候选占比。
- Dense、Sparse、Hybrid、Diversified、Reranked 分阶段消融指标。

### 20.4 生成指标

- Citation Validity：发布引用必须为 100%。
- Citation Precision/Recall。
- Claim Faithfulness 和 Claim Completeness。
- 跨论文归纳正确率。
- 冲突识别率和证据不足拒答率。
- 数字、单位、算法名和数据集原子准确率。
- 未授权信息泄露率必须为 0。

### 20.5 建议首轮准入门槛

以下是候选门槛，应在建立真实数据集后冻结到评测契约：

| 指标 | 建议门槛 |
| --- | ---: |
| Paper Recall@10 | `>= 0.90` |
| Evidence Recall@20 | `>= 0.90` |
| 多论文 Document Coverage Recall | `>= 0.85` |
| Hybrid nDCG@10 | 不低于现有方案，目标相对提升 `>= 5%` |
| Citation Validity | `1.00` |
| Citation Precision | `>= 0.95` |
| Claim Faithfulness | `>= 0.95` |
| 无知识拒答率 | `>= 0.95` |
| 权限绕过拦截率 | `1.00` |
| P95 总延迟 | 不超过冻结基线 `30%`，或满足产品明确 SLA |

Embedding v4、Cross-Encoder、NLI 和 Qdrant 应分别做消融，不把多个变化合并后只看最终答案，以免无法定位收益来源。

## 21. 分阶段实施路线

### 阶段 0：冻结多论文基线

> 实施状态（2026-08-25）：阶段 0 已完成。15 篇论文以单一 release
> `b17672e25ed44ee793a8799def2d968e` 入库，共 974 个节点；42 条黄金问题已
> 完成全量评测，结果冻结于 `config/rag_multi_paper_baseline_v1.json`。
> baseline 如实保留 2 个 DashScope 45 秒超时和未达建议门槛项，作为后续
> 阶段统一改进对照，不追溯性修改 v1。

- 选择代表性的中英文异常检测论文。
- 建立解析、召回、比较、综合、负样本和权限评测集。
- 冻结当前 Chroma/v2/现有 Prompt 的基线结果。
- 为现有 P0 契约增加“允许内部扩展但公开签名不变”的迁移约束。

退出条件：评测集、指标定义、基线报告和回滚点齐备。

### 阶段 1：论文解析与 PaperDocument v2

- 接入 Docling 适配器和解析路由。
- 接入 GROBID 元数据补充，保留 MarkItDown 回退。
- 扩展 DocStore schema、解析诊断和稳定 ID。
- 暂不改变在线检索，先验证解析产物和可重建性。

退出条件：解析质量通过，旧文档和非论文格式无回归。

### 阶段 2：父子节点和多粒度 Chroma 索引

- 构建论文级表示和 Chunk 级父子节点。
- 扩展 Manifest 为多索引原子发布。
- 使用现有 v2 模型先验证“结构与粒度”本身的收益。
- 加入父章节和邻接关系的精排后扩展。

退出条件：多粒度索引可完整对账，检索指标不退化。

### 阶段 3：Embedding v4 灰度

- 构建 1024 维 v4 影子 release。
- A/B v2 与 v4 的 Dense、Hybrid、跨语言和精确术语指标。
- 评估 Embedding 成本、构建耗时、索引体积和查询延迟。
- 达标后原子切换，不保留混合向量 collection。

退出条件：质量收益达到冻结门槛且性能、费用可接受。

### 阶段 4：查询改写、分解和跨论文检索

- 增加查询类型判断和结构化独立问题改写。
- 只对比较/综合问题启用 2–5 个子查询。
- 增加论文级粗筛、RRF 多查询融合和 Document-aware 多样性。
- 扩展检索审计到子查询和论文覆盖维度。

退出条件：多轮和多论文 Coverage 明显提升，简单问题延迟无显著回归。

### 阶段 5：默认重排、证据矩阵和综合生成

- 默认启用审核后的多语言 Cross-Encoder。
- 加入 Evidence Matrix 和多论文上下文预算分配。
- 增加比较/综合 Prompt 和跨论文 Grounding 规则。
- 保留 RRF、旧 Context 和旧 Prompt 快速回退开关。

退出条件：Citation、Faithfulness、Completeness 和延迟通过门槛。

### 阶段 6：NLI 灰度

- 在确定性校验之后加入独立 NLI。
- 先只记录不阻断，再评估误杀和漏检。
- 根据事实类别决定 fail-open/fail-closed；关键数值和权限仍确定性优先。

退出条件：语义支持准确率提升，且不会降低安全边界。

### 阶段 7：Qdrant 迁移

- 实现 Qdrant `VectorStore/VectorIndexWriter` 适配器。
- 双写 Chroma/Qdrant 影子索引并校验 Node 集合等价。
- 对 Dense、Sparse、过滤、RRF、容量和并发做 A/B。
- 小流量读 Qdrant，Chroma 保持可回退。
- 观察期通过后将 Qdrant 设为目标 Profile；原 Chroma release 延迟清理。

退出条件：功能等价、质量不退化、稳定性和运维预案通过。

## 22. 主要风险与控制

| 风险 | 控制措施 |
| --- | --- |
| Docling/GROBID 引入较重依赖 | 适配器隔离；支持进程外服务；解析并发限流；MarkItDown 受控回退 |
| 两套解析结果冲突 | 字段来源、置信度和冲突诊断；关键字段不静默覆盖 |
| 父子节点导致索引膨胀 | 仅对必要粒度向量化；父节点可只存 DocStore；量化和容量评测 |
| 查询分解产生错误子问题 | 结构化输出、术语保留、数量上限、主查询并行召回 |
| 多样性降低纯相关性 | 使用软配额和相关性下限，不强制填充低分论文 |
| Cross-Encoder 延迟或 OOM | 批次、候选上限、deadline、懒加载、RRF 回退 |
| 邻接扩展引入噪声 | 只在精排后按关系扩展，并重新去重和装箱 |
| NLI 自身误判 | 位于确定性校验之后；先 shadow 审计；模型版本固定 |
| Qdrant 双写不一致 | Release Node 集合哈希、逐索引对账、单一原子发布指针 |
| 多版本论文重复计数 | 引入 `work_id`，引用仍指向具体 document version |
| 上下文变大导致生成成本上涨 | 证据矩阵分配预算、父节点摘取、硬 Token 上限和成本审计 |

## 23. 最终验收定义

完成本方案不等于“安装了解析器或切换了向量库”，而是同时满足：

1. 论文 PDF 能稳定恢复可追溯的章节、表格、公式和图注结构。
2. 原文件、PaperDocument、父子 Node、论文索引和 Chunk 索引可以从同一 release 完整对账。
3. 简单事实问题不因新流程明显增加延迟或降低准确率。
4. 跨论文问题能召回多篇独立、互补且相关的证据，而非由单篇论文占满上下文。
5. 查询改写和分解不会改变算法名、数据集、指标和用户限定条件。
6. Cross-Encoder 故障、NLI 故障和 Qdrant 故障均有确定性回退路径。
7. 最终发布的每个 Claim 都能定位到用户有权访问的具体论文、章节、页码和 Node。
8. 跨论文共识、差异和优劣判断满足专门的证据覆盖规则。
9. 无答案、证据冲突或证据不足时能够明确拒答或说明缺口。
10. 全链路模型、配置、Prompt、release、候选和引用均可审计与复现。

## 24. 与现有文档的关系

本方案是现有 P0–P5 架构的后续演进设计，不替代已有契约文档：

- [`README.md`](./README.md)：当前 RAG 分层入口。
- [`P1_INGESTION.md`](./P1_INGESTION.md)：原文件、DocStore、影子索引与发布事务。
- [`P2_NODES.md`](./P2_NODES.md)：现有稳定 Node 与语义切分契约。
- [`P3_INDEXING.md`](./P3_INDEXING.md)：Embedding 与蓝绿索引。
- [`P4_CONTEXT.md`](./P4_CONTEXT.md)：严格 Context Packing。
- [`P5_GROUNDED_GENERATION.md`](./P5_GROUNDED_GENERATION.md)：Claim、引用与可信生成。
- [`docs/rag-evaluation.md`](../../../docs/rag-evaluation.md)：现有检索评测基线。

后续实施时，应为每个阶段分别建立迁移文档和冻结契约，避免将本设计文档直接当作某一版本的已实现行为说明。
