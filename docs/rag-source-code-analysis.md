# RAG 模块完整工作流程分析

> 本文严格依据当前项目运行时代码。项目采用的是 **DashScope Embedding + LlamaIndex + Chroma + BM25 + Qwen** 方案，不使用 Faiss，也没有使用 LangChain。
>
> 需要特别注意：只有被判定为“内部系统/知识库问题”的请求才进入 RAG；普通问题直接走通用 LLM 模式。

## 1. 模块概述

RAG 模块用于把管理员上传的工业异常检测资料、服务器说明、数据集文档等转化为可检索知识，并在用户提问时：

1. 根据当前用户权限确定可访问文档；
2. 通过向量检索和 BM25 检索召回相关文档块；
3. 可选使用 Cross-Encoder 重排；
4. 在固定 Token 预算内组装带 `[K1]`、`[K2]` 编号的上下文；
5. 调用通义千问生成结构化 Claim；
6. 在服务端校验证据、引用和关键技术原子；
7. 仅向用户发布通过校验的内容。

整体设计目标不只是提高回答相关性，还包括：

- 文档格式统一和来源可追溯；
- 索引版本原子发布；
- 文档级访问控制；
- 防止知识文档中的 Prompt Injection；
- 防止模型生成无效引用或无依据结论；
- 向量库、Embedding 和模型适配器可替换。

领域接口与厂商 SDK 被明确隔离在 [`contracts.py`](../fastapi-app/services/rag/core/contracts.py)，整体分层说明见 [`RAG README`](../fastapi-app/services/rag/README.md)。

---

## 2. 工作流程图

系统实际上包含两条关联流水线。

### 2.1 离线入库和索引构建

```text
管理员上传文件
    │
    ├─ 校验扩展名、文件大小和非空内容
    ▼
MarkItDownDocumentLoader
    │  PDF/DOCX/PPTX/XLSX/TXT/Markdown...
    ▼
统一 Markdown Document
    │
    ├─ PDF 页码、重复页眉页脚清理
    └─ PDF 编号标题转换为 Markdown 标题
    ▼
MarkdownNodeParser
    │
    ├─ 标题路径解析
    ├─ 段落、表格、代码块、命令块识别
    ├─ Token 预算分块
    └─ 生成稳定 document_id / node_id
    ▼
原始文件存储 + JSON DocStore
    │
    ▼
DashScope text-embedding
    │  text_type="document"
    ▼
L2 归一化浮点向量
    │
    ▼
LlamaIndex VectorStoreIndex
    │
    ▼
Chroma cosine 影子 Collection
    │
    ├─ 完整性、Node ID、向量维度校验
    ▼
原子切换 active release 指针
    ▼
新索引开始服务在线检索
```

上传入口及影子发布事务位于 [`knowledge.py`](../fastapi-app/api/knowledge.py)。

### 2.2 在线问答

```text
用户问题 + 会话历史 + 服务端可信身份
    │
    ▼
HistoryAwareQueryTransformer
    │  短追问/指代问题补充最近用户问题
    ▼
QueryModeRouter
    │
    ├─ general ───────────────→ 通用 Qwen 回答，不使用知识库
    │
    └─ knowledge_base
          │
          ▼
      解析当前 Release 中授权 doc_id
          │
          ├─ Chroma cosine Dense Top-50
          └─ BM25 Sparse Top-50
          │
          ▼
      服务端二次 ACL 过滤
          │
          ▼
      RRF 融合、近重复去除
          │  最多保留 100 个联合候选
          ▼
      可选 Cross-Encoder 重排
          │  默认最终 Top-8
          ▼
      ContextPacker
          │  2800 Token 硬预算
          │  生成 [K1]、[K2]...
          ▼
      GroundedPromptBuilder
          │  问题 + 历史 + 知识上下文 + 允许引用集合
          ▼
      Qwen JSON Object 生成
          │
          ▼
      GroundedAnswerValidator
          │
          ├─ JSON Schema 校验
          ├─ 引用权限/存在性校验
          ├─ 命令、路径、数字等原子校验
          └─ Claim 词面支持校验
          ▼
      服务端 Markdown 渲染
          ▼
      SSE 分块返回并保存会话
```

聊天入口和 SSE 返回过程见 [`chat.py`](../fastapi-app/api/chat.py)。

---

## 3. 核心阶段详解

### 3.1 文档预处理与索引构建

#### 3.1.1 文件接收与统一转换

管理员上传接口支持：

- `.txt`
- `.md`、`.markdown`
- `.pdf`
- `.docx`
- `.pptx`
- `.xlsx`、`.xls`、`.csv`
- `.html`、`.htm`
- `.json`、`.xml`
- `.ipynb`
- `.epub`

上传大小默认限制为 20 MB，并采用分段读取方式在服务端强制检查，不能依赖前端绕过。

文件通过 `MarkItDown.convert_stream()` 从内存字节流统一转换为 Markdown，而不是把文件名当作本地路径或 URL。转换结果形成：

```python
Document(
    text=markdown,
    metadata={
        "filename": ...,
        "extension": ...,
        "content_format": "markdown",
        "converter": "markitdown",
    },
)
```

对应实现见 [`loading.py`](../fastapi-app/services/rag/document/loading.py)。

#### 3.1.2 PDF 专项清理

PDF 转换后还会执行保守清洗：

- 识别页码和分页标记；
- 文档至少三页时，统计每页前后三个非空行；
- 某页眉/页脚在至少 `max(3, ceil(页数 × 0.6))` 页重复时，将其视为重复边界；
- 重复页眉保留第一次出现，后续删除；
- 删除重复页脚；
- 将 `1`、`1.2`、`第一章`、`一、` 等短标题转成 Markdown 标题；
- 围栏代码块内部不做标题识别。

扫描型 PDF 不包含有效文本时，当前代码不会执行 OCR，只会在预览接口中给出“OCR”建议。

#### 3.1.3 Markdown 语义分块

分块不是简单的固定字符切割，而是分成两个步骤。

第一步，解析语义单元：

- Markdown 标题用于维护 `heading_path`；
- 空行作为普通段落边界；
- Markdown 围栏代码块保持完整；
- 表格、缩进代码和命令块标记为 `protected`；
- 记录字符起止位置、标题路径和块类型。

第二步，将语义单元组合成 Chunk：

- 默认硬上限：500 Token；
- 默认目标大小：`500 × 0.8 = 400 Token`；
- 默认最小参考值：`500 × 0.2 = 100 Token`；
- 默认重叠预算：50 Token；
- 重叠以完整语义单元为单位，不截断段落；
- 普通超长段落先按句子切分，再按 Token 预算切分；
- 表格、命令和代码等受保护内容不会被拦腰切断；
- 单个受保护块超过 500 Token 时仍保持完整，并标记 `oversized_protected=true`。

核心算法见 [`splitting.py`](../fastapi-app/services/rag/document/splitting.py)。

分块阶段使用 LlamaIndex 的离线 tokenizer 统计 Token，并为每个节点生成稳定 ID。节点元数据包括：

```text
document_id
node_id
chunk_index
source_filename
source_sha256
section_path
char_start / char_end
line_start / line_end
position
citation_label
previous_node_id / next_node_id
```

稳定 Node 生成见 [`parsing.py`](../fastapi-app/services/rag/document/parsing.py)。

#### 3.1.4 Embedding

默认模型为 DashScope `text-embedding-v2`。

文档向量化时明确传递：

```text
text_type = "document"
```

查询向量化则传递：

```text
text_type = "query"
```

Embedding 适配器会：

- 根据模型限制批大小：v2 最大 25，v3/v4 最大 10；
- 按 `text_index` 恢复返回顺序；
- 检查向量数量、数值类型、NaN/Infinity、零范数和维度一致性；
- 对所有向量执行 L2 归一化；
- 对网络错误、408、409、429 和 5xx 做指数退避重试。

实现见 [`embedding.py`](../fastapi-app/services/rag/indexing/embedding.py)。

#### 3.1.5 Chroma 索引和蓝绿发布

项目没有使用 Faiss。向量存储是本地持久化 Chroma，Collection 明确配置：

```python
"hnsw:space": "cosine"
```

LlamaIndex 的 `embed_nodes()` 负责 Embedding 编排，`VectorStoreIndex` 负责把预先附加向量的 `TextNode` 批量写入 Chroma。

每次上传不是直接修改在线 Collection，而是：

1. 原文件按 SHA256 内容寻址保存；
2. Markdown、Node 和诊断信息写入 JSON DocStore；
3. 从 DocStore 读取当前所有发布文档；
4. 创建 `knowledge_shadow_<release_id>`；
5. 全量重新构建影子索引；
6. 校验 Node ID 集合、重复项和向量维度；
7. 写入不可变 Release Manifest；
8. 在 MySQL 事务中原子切换 active release 指针；
9. 发布失败时恢复旧指针并清理影子 Collection。

Chroma 只是可重建的派生索引，原始文件和 DocStore 才是事实源，见 [`storage.py`](../fastapi-app/services/rag/document/storage.py)。影子索引构建见 [`knowledge_service.py`](../fastapi-app/services/knowledge_service.py)。

### 3.2 检索（Retrieval）

#### 3.2.1 查询改写与模式路由

系统不会对所有问题执行 RAG。

对于长度不超过 16 字或包含“这个、它、上述、然后呢”等指代词的追问，代码把最近两个用户问题拼接为：

```text
历史问题：...
当前问题：...
```

该过程不调用 LLM，见 [`prompting.py`](../fastapi-app/services/rag/answering/prompting.py)。

随后通过正则判断模式：

- 出现“本系统、知识库、服务器、GPU、CUDA、训练任务、数据集、异常检测、PBAS”等内部关键词时进入 `knowledge_base`；
- 普通常识问题进入 `general`；
- 试图绕过权限、读取隐藏文档的请求也会强制进入受控知识库模式。

路由规则见 [`grounding.py`](../fastapi-app/services/rag/answering/grounding.py)。

#### 3.2.2 权限下推

进入知识库模式后，代码首先根据当前 Release Manifest 和可信用户身份计算授权 `doc_id`。

Dense 检索时把权限下推为：

```python
where={"doc_id": {"$in": sorted(allowed_doc_ids)}}
```

这样未经授权的文档不会参与 Chroma Top-K 排序。

不过代码不把 Chroma 过滤当作最终安全边界。Dense 和 BM25 返回结果都还会经过一次服务端 ACL 过滤，防止索引元数据或查询适配器异常导致越权。

实现见 [`pipeline.py`](../fastapi-app/services/rag/search/pipeline.py)。

#### 3.2.3 Dense 向量召回

查询文本使用同一个 DashScope Embedding 模型转换为归一化向量，并明确采用 `text_type="query"`。

默认 Dense 参数：

```text
Top-K = 50
相似度阈值 = 0.20
```

Chroma 返回的是 cosine distance，代码转换为：

```text
similarity score = 1 - cosine distance
```

因此对外的 `score` 越大越相关，而 `distance` 越小越相关。具体转换见 [`vector_store.py`](../fastapi-app/services/rag/indexing/vector_store.py)。

#### 3.2.4 BM25 字面召回

BM25 索引按 Release 缓存，仅在发布版本变化时重建。

分词策略为：

- 英文、路径、命令和带分隔符的技术词保留为完整 Token；
- 中文使用二元组；
- 标题路径和正文共同进入 BM25 文档；
- 默认参数 `k1=1.5`、`b=0.75`；
- 默认返回 Top-50；
- 只返回 BM25 分数大于零的结果；
- BM25 排序前同样支持授权 `doc_id` 过滤。

代码没有依赖 `rank-bm25`，而是自行实现公式，见 [`lexical.py`](../fastapi-app/services/rag/search/lexical.py)。

#### 3.2.5 RRF 融合和去重

Dense 与 BM25 候选使用 Reciprocal Rank Fusion：

```text
RRF score += 1 / (60 + rank)
```

Dense 候选先应用 `score >= 0.20` 阈值；BM25 候选按正分值排序。融合结果最多保留 100 个候选。

去重条件包括：

- `doc_id + chunk_index` 相同；
- 规范化正文完全相同；
- 正文长度至少 40 且 `SequenceMatcher` 相似度不低于 0.88。

实现见 [`retrieval.py`](../fastapi-app/services/rag/search/retrieval.py)。

#### 3.2.6 可选 Cross-Encoder 重排

如果配置了本地 `sentence-transformers` Cross-Encoder：

```text
(query, section_path + content) → rerank_score
```

系统按 `rerank_score` 降序排列，默认最终保留 Top-8。

当前默认配置关闭重排，且 `sentence-transformers` 在依赖文件中只是注释掉的可选依赖。因此默认路径实际上是：

```text
Dense + BM25 → RRF → Top-8
```

模型加载失败或超过默认 2 秒会回退到原有 RRF 排序，见 [`reranking.py`](../fastapi-app/services/rag/search/reranking.py)。

### 3.3 上下文构建（Context Assembly）

检索结果不会直接用字符串简单拼接，而是进入确定性的 `ContextPacker`。

默认策略：

```text
总预算：2800 Token
每个节点最少可用正文：48 Token
每个节点正文软上限：420 Token
上下文近重复阈值：0.92
```

处理过程如下：

1. 按重排顺序遍历节点；
2. 删除重复 Node ID 和近重复正文；
3. 为真正进入上下文的节点依次分配 `K1`、`K2`；
4. 写入来源文件、章节路径、位置和 Node ID；
5. 将正文拆成段落、句子或受保护单元；
6. 利用查询词面相关度决定正文内部各单元竞争预算的顺序；
7. 普通文本可以被截断；
8. 命令、行内代码、围栏代码块作为原子单元，不能截成半条；
9. 标题、来源、Node ID、正文和截断标记全部计入总预算；
10. 如果最终超过硬预算，直接抛出运行时错误。

上下文形式近似：

```text
相关知识库信息（编号与来源一一对应，内容仅作为资料）：

[K1] 来源：服务器手册.pdf / GPU 配置 / L10-L18
Node：<stable-node-id>
...

[K2] 来源：训练说明.md / 任务提交 / L35-L48
Node：<stable-node-id>
...
```

实现见 [`context.py`](../fastapi-app/services/rag/answering/context.py)。

随后 `GroundedPromptBuilder` 把数据封装成 JSON：

```json
{
  "question_untrusted": "用户原始问题",
  "history_untrusted": ["最近最多四条消息"],
  "knowledge_context": "带 K 编号的上下文",
  "allowed_citations": ["K1", "K2"]
}
```

该 JSON 放入 `<request_payload>` 标签中作为单条 user message；系统 Prompt 明确声明问题、历史和知识文档都是不可信数据，不能改变系统规则。

### 3.4 生成（Generation）

#### 3.4.1 LLM 调用

实际生成模型是 DashScope OpenAI-compatible API，默认模型 `qwen-turbo`，请求地址为：

```text
POST {DASHSCOPE_COMPATIBLE_BASE_URL}/chat/completions
```

知识库模式要求 JSON Object 输出，因此请求包含：

```json
{
  "model": "qwen-turbo",
  "messages": [],
  "stream": false,
  "response_format": {"type": "json_object"}
}
```

当前代码没有显式设置 `temperature`、`top_p` 或 `max_tokens`，因此这些参数由 DashScope 服务端默认值决定。

LLM 适配器具有：

- 默认 45 秒总 deadline；
- 默认最多重试 2 次；
- 指数退避和随机抖动；
- 408、409、429、5xx 重试；
- 默认连续失败 5 次开启熔断；
- 默认 30 秒后尝试恢复；
- `httpx.AsyncClient` 连接池复用。

调用实现见 [`llm_service.py`](../fastapi-app/services/llm_service.py)。

#### 3.4.2 结构化回答与 Grounding 校验

模型必须输出：

```json
{
  "mode": "knowledge_base",
  "refusal": false,
  "claims": [
    {
      "text": "一个可验证结论",
      "citations": ["K1"]
    }
  ]
}
```

服务端随后验证：

- 输出必须是合法 JSON 对象；
- `mode` 必须为 `knowledge_base`；
- 非拒答必须包含 Claim；
- 最多允许 12 个 Claim；
- 每个 Claim 必须有引用；
- 引用必须存在于本次 `allowed_citations`；
- 命令、路径、URL、数字等关键原子必须原样存在于引用证据；
- Claim 与证据的加权词面支持度必须至少为 0.30；
- 不受支持的 Claim 被直接丢弃；
- 所有 Claim 都不受支持时，触发一次受控重新生成；
- 第二次仍不合格则拒绝发布。

通过验证后，服务端自行渲染 Markdown 和 `[Kx]` 引用。模型无权决定最终展示层，见 [`rendering.py`](../fastapi-app/services/rag/answering/rendering.py)。

#### 3.4.3 返回方式

虽然 HTTP 接口使用 SSE，但底层不是 Token 级 LLM 流式输出：

1. `stream=false` 等待完整模型响应；
2. 完整执行 Grounding 校验；
3. 服务端按约 96 字符切块；
4. 通过 SSE 逐块发送；
5. 完整回答写入消息数据库。

这种设计会增加首字延迟，但避免把尚未校验的模型内容提前发送给用户。

---

## 4. 关键组件与依赖

| 组件 | 用途 | 当前状态 |
|---|---|---|
| FastAPI | 上传、聊天和 SSE API | 必选 |
| MarkItDown 0.1.6 | PDF、Office、文本等统一转 Markdown | 必选 |
| LlamaIndex Core 0.14.23 | `Document`、`TextNode`、Tokenizer、Embedding 编排、`VectorStoreIndex` | 必选 |
| llama-index-vector-stores-chroma 0.5.5 | LlamaIndex 到 Chroma 的适配 | 必选 |
| ChromaDB 1.5.9 | 本地持久化向量数据库，cosine HNSW 空间 | 必选 |
| DashScope 1.25.16 | `text-embedding-v2` Embedding | 必选 |
| Qwen / DashScope compatible API | 最终回答生成 | 必选 |
| httpx | 异步 LLM HTTP 调用和连接池 | 代码直接使用 |
| 自研 BM25 | 中文二元组、技术词稀疏检索 | 内置 |
| sentence-transformers CrossEncoder | 可选精排 | 默认未安装、未启用 |
| MySQL/Tortoise ORM | 文档元数据、会话和检索审计 | 系统依赖 |

依赖版本见 [`requirements.txt`](../requirements.txt)。

当前代码中：

- 没有 Faiss；
- 没有 LangChain；
- Hugging Face Transformers 不是主链路依赖；
- Sentence-Transformers 只用于可选 Cross-Encoder 重排。

---

## 5. 数据流与输入输出

| 阶段 | 输入 | 输出 |
|---|---|---|
| 文件上传 | `UploadFile` | `bytes` |
| 文档加载 | `bytes + filename` | Markdown 字符串 |
| 预处理 | Markdown `Document` | 清理后的 Markdown + diagnostics |
| 分块 | Markdown 字符串 | `Node[]` |
| Node 内容 | 字符串 | `text + metadata + stable node_id` |
| 文档 Embedding | `list[str]` | `list[list[float]]`，L2 归一化 |
| 索引写入 | `TextNode + embedding` | Chroma Collection |
| 查询改写 | 当前问题 + 历史消息 | 检索查询字符串 |
| 查询 Embedding | 查询字符串 | 单个浮点向量 |
| Dense 检索 | 查询向量 | 文档块、cosine distance、similarity score |
| BM25 检索 | 查询字符串 | 文档块、`bm25_score` |
| RRF 融合 | 两组排名 | 候选字典列表、`fusion_score` |
| 重排 | 查询 + 候选文本 | `rerank_score` 排序结果 |
| Context Packing | 排序后的候选 | `PackedContext` |
| Prompt 构建 | 问题、历史、PackedContext | Chat Completion messages |
| Qwen 生成 | messages + system prompt | JSON 字符串 |
| Grounding 校验 | JSON + PackedContext | `VerifiedAnswer` |
| 展示 | Verified Claims | Markdown 文本 + 来源信息 |
| HTTP 返回 | Markdown 文本 | SSE `status/content/done` 事件 |

---

## 6. 配置与可调参数

默认值集中在 [`settings.py`](../fastapi-app/settings.py)。

### 文档和 Embedding

| 环境变量 | 默认值 | 作用 |
|---|---:|---|
| `AI_RAG_CHUNK_TOKENS` | 500 | Chunk Token 硬上限 |
| `AI_RAG_OVERLAP_TOKENS` | 50 | 完整语义单元重叠预算 |
| `AI_RAG_MAX_UPLOAD_BYTES` | 20 MB | 上传文件上限 |
| `DASHSCOPE_EMBEDDING_MODEL` | `text-embedding-v2` | Embedding 模型 |
| `AI_EMBEDDING_BATCH_SIZE` | 25 | Embedding 请求批大小 |
| `AI_EMBEDDING_MAX_RETRIES` | 3 | Embedding 最大重试 |
| `AI_EMBEDDING_RETRY_BACKOFF_SECONDS` | 0.5 | Embedding 退避基数 |
| `AI_RAG_INGESTION_CONCURRENCY` | 1 | 入库并发数 |

### 检索和重排

| 环境变量 | 默认值 | 实际作用 |
|---|---:|---|
| `AI_RAG_DENSE_CANDIDATE_K` | 50 | 在线 Dense 粗召回数量 |
| `AI_RAG_LEXICAL_CANDIDATE_K` | 50 | 在线 BM25 粗召回数量 |
| `AI_RAG_CANDIDATE_UNION_LIMIT` | 100 | RRF 联合候选上限 |
| `AI_RAG_RERANK_FINAL_K` | 8 | 在线重排后最终候选数，运行时限制为 4～8 |
| `AI_RAG_SCORE_THRESHOLD` | 0.20 | Dense similarity 下限 |
| `AI_RAG_HYBRID_ENABLED` | `true` | 是否启用混合检索 |
| `AI_RAG_BM25_ENABLED` | `true` | 是否启用 BM25 |
| `AI_RAG_ACL_PUSHDOWN_ENABLED` | `true` | 是否向 Chroma 下推 doc_id ACL |
| `AI_RAG_RERANKER_ENABLED` | `false` | 是否开启 Cross-Encoder |
| `AI_RAG_RERANKER_MODEL` | 空 | 本地 Cross-Encoder 模型名 |
| `AI_RAG_RERANKER_TIMEOUT_SECONDS` | 2 | 精排超时 |

`AI_RAG_CANDIDATE_K=8` 和 `AI_RAG_FINAL_K=4` 主要服务于旧兼容检索路径；当前授权检索主链路使用的是 Dense 50、BM25 50、联合 100、最终 8。

`AI_RAG_LEXICAL_MIN_SCORE=0.08` 也只在旧的逐块词面打分兼容路径生效；当前 BM25 主路径保留所有 BM25 分数大于零的 Top-50。

### 上下文和生成

| 环境变量 | 默认值 | 作用 |
|---|---:|---|
| `AI_RAG_CONTEXT_TOKENS` | 2800 | 上下文硬预算 |
| `AI_RAG_CONTEXT_MIN_NODE_TOKENS` | 48 | 接收一个 Node 所需最小正文空间 |
| `AI_RAG_CONTEXT_MAX_NODE_TOKENS` | 420 | 每个 Node 正文软上限 |
| `AI_RAG_CONTEXT_DUPLICATE_SIMILARITY` | 0.92 | 上下文近重复阈值 |
| `AI_RAG_QUERY_HISTORY_TURNS` | 2 | 检索查询引用的历史用户问题数 |
| `AI_RAG_CLAIM_LEXICAL_SUPPORT` | 0.30 | Claim 最低词面证据支持 |
| `AI_RAG_GROUNDING_VALIDATION_RETRIES` | 1 | 校验失败后的受控重生成次数 |
| `DASHSCOPE_MODEL` | `qwen-turbo` | 生成模型 |
| `AI_LLM_TIMEOUT_SECONDS` | 45 | LLM 总 deadline |
| `AI_LLM_MAX_RETRIES` | 2 | LLM 最大重试 |
| `AI_LLM_CIRCUIT_FAILURE_THRESHOLD` | 5 | 熔断失败阈值 |
| `AI_LLM_CIRCUIT_RECOVERY_SECONDS` | 30 | 熔断恢复等待时间 |

提示模板不是环境变量，而是代码常量：

- 知识库 Prompt 版本：`grounded-knowledge-v4`
- 通用 Prompt 版本：`general-assistant-v2`

还有一个需要注意的实现细节：`AI_RAG_FAITHFULNESS_THRESHOLD=0.90` 会传入 `GroundedAnswerValidator`，但当前 `validate()` 只计算 `supported / claims_raw` 并用于返回和审计，没有用该阈值拒绝回答。真正的发布门禁是每条 Claim 的原子匹配和 `0.30` 词面支持度。

---

## 7. 潜在优化点

### 7.1 让 Faithfulness 配置真正参与发布决策

当前代码声明并加载了 `AI_RAG_FAITHFULNESS_THRESHOLD=0.90`，但最终没有比较：

```text
faithfulness >= minimum_faithfulness
```

因此候选回答即使只有少量 Claim 通过，其余 Claim 全被丢弃，只要至少留下一条就仍会发布。逐 Claim 过滤本身是安全的，但配置名称和实际行为不一致。

可以选择：

- 当整体 Faithfulness 低于阈值时触发受控重生成或拒答；或
- 明确把该字段改名为审计指标，避免运维人员误以为它是生效中的发布门禁。

### 7.2 复用未变化 Node 的 Embedding

当前上传采用全量蓝绿重建。即使只新增或修改一份文档，`_build_shadow_release()` 也会从 DocStore 读取所有 Node，并为整个候选索引重新生成 Embedding。

可以按以下键建立 Embedding 缓存：

```text
embedding_provider
+ embedding_model
+ embedding_schema_version
+ node_id
+ text_sha256
```

影子索引仍然保持全量构建和原子发布，但未变化 Node 可以复用已验证向量。这样能够显著降低大知识库更新时的 DashScope 调用量、发布时间和失败概率，同时不破坏现有蓝绿发布一致性。
