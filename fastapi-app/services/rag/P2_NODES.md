# RAG P2 节点解析与分块

## 第一性原则

一个可用的检索 Node 必须同时满足四个条件：它能独立理解、能回到
原文、在相同输入和版本下可重现，且不破坏不可分割的操作语义。
Token 上限是模型工程约束，命令完整性是正确性约束；两者冲突时后者
优先，并显式标记超长节点。

## 实现流程与组件

1. `split_paragraphs_with_headings` 扫描 Markdown，维护 1–6 级标题栈，
   并记录每个语义块的原文字符区间。围栏内的 `#` 不会误识别为标题。
2. 语义块分类器把围栏代码、Shell/运维命令、Markdown 表格和缩进代码
   标记为 `protected`，这些块只能整体进入 Node。
3. 普通超长段落先按中英文句子边界切分，单句仍超长时再按
   LlamaIndex 默认 tokenizer 的 Token 预算切分。
4. `chunk_paragraphs` 按完整语义块贪心聚合，默认目标为上限的 80%，
   合格下限为 20%，并仅以完整语义块做重叠。
5. `llamaindex_parser.MarkdownNodeParser` 继承 LlamaIndex `NodeParser`，在 `_parse_nodes`
   中生成原生 `TextNode`；继承的后处理为同源 Node 建立
   `SOURCE/PREVIOUS/NEXT` 关系。
6. `parse(contracts.Document)` 是适配器边界，它把原生 TextNode 转回
   框架无关的领域 `Node`，避免 LlamaIndex SDK 泄漏到应用层。

## 稳定 Node ID

Node ID 是下列规范化身份 JSON 的 SHA256：

```text
parser_schema_version
+ document_id
+ char_start / char_end
+ section_path
+ SHA256(node_text)
```

ID 不包含随机数、运行时时间或 embedding 结果。分块算法变更时必须提升
`PARSER_SCHEMA_VERSION` 和入库 schema，以新的不可变 Document 版本发布，
不得覆盖旧 DocStore。

## Node metadata 契约

| 类别 | 字段 |
| --- | --- |
| 身份 | `document_id`, `chunk_index`, `parser_schema_version`, `llama_node_type` |
| 来源 | `source_filename`, `source_sha256`, `source_uri`, `source_node_id` |
| 章节 | `heading_path`, `heading_paths`, `section_path` |
| 位置 | `char_start`, `char_end`, `line_start`, `line_end`, `position` |
| 引用 | `citation_label` |
| Token | `token_count`, `token_min`, `token_target`, `token_max`, `within_target_range` |
| 结构保护 | `block_types`, `protected`, `oversized_protected` |
| 链接 | `previous_node_id`, `next_node_id` |

存入 Chroma 前只保留标量 metadata；DocStore 保留全部字段。Embedding 仅对
Node 正文计算，不把内部位置字段混入语义向量。

## 技术选型与难点

- 仅引入 `llama-index-core`，不引入全量 provider 集成，减少 SDK 冲突和
  供应链面。固定到已验证版本，升级时重跑契约测试。
- PDF 标题识别可能失真：继续保留 P1 清洗诊断和原文；章节不确定时使用
  `[root]`，不伪造层级。
- 一个命令/表格可能超过 Node 上限：保留完整并标记
  `oversized_protected`；P3 上下文构建可用摘要+原文引用处理。
- 短文档的尾 Node 可能低于下限；验收用代表性文档集统计，不通过
  重复填充或破坏章节语义来伪造 Token 达标。
- LlamaIndex 的现行基类名为 `NodeParser`（早期方案常写
  `BaseNodeParser`），实现以当前 core API 为准。

## 运维与验收

```bash
# 运行 P2 分块契约
python3 -m pytest tests/test_rag_p2_nodes.py -q

# 只构建存量文档的 P2 影子索引，不改当前版本
python3 scripts/migrate_rag_nodes_p2.py

# MySQL 事务内更新文档指针、发布并执行四方对账
python3 scripts/migrate_rag_nodes_p2.py --publish

# 验证后丢弃未发布的影子 collection
python3 scripts/migrate_rag_nodes_p2.py --discard
```

自动化验收覆盖：所有 Node 的稳定身份/来源/章节/位置、命令块不跨块、
95% Token 区间、同版本重建 ID 和数量一致，以及存量迁移不修改
当前发布指针。
