# RAG P1 文档入库与影子索引

## 第一性原则

RAG 检索中真正不可替代的数据只有两类：用户上传的原始字节，以及从
这些字节中可重现得到的统一 Document/Node。Embedding 和 Chroma 都是
可丢弃派生物，不应被当作事实源。

因此 P1 不在发布 collection 上做增量修补，而是先构建一个完整候选版，
通过后只替换一个原子发布指针。

## 核心组件

| 组件 | 实现 | 职责与选型考量 |
| --- | --- | --- |
| 内容寻址文件库 | `ContentAddressedFileStore` | 以 SHA256 作为路径，同内容只存一份；不使用用户文件名组路径，防止越界。 |
| 统一 DocStore | `JsonDocumentStore` | 保存 Markdown Document、来源、诊断数据和全部 Node；JSON 便于审计、迁移和 LlamaIndex 适配。 |
| 确定性标识 | `deterministic_document_id/node_id` | Document 由文件名+原文件哈希确定；Node 由文档、序号、位置和正文哈希确定。 |
| 发布清单 | `ReleaseManifestStore` | 记录候选文档集、Node 集合哈希、模型和维度。 |
| 影子索引 | `knowledge_shadow_<release_id>` | 只从 DocStore 全量构建；P3 由 LlamaIndex 去重后写入全新 collection。 |
| 发布指针 | `active_release.json` | 通过同目录临时文件 + `fsync` + `os.replace` 单文件原子切换。 |
| 一致性对账 | `reconcile_metadata` | 比较 MySQL 文档指针、原文件哈希、DocStore Node 和 Chroma Node。 |

## 上传与发布流程

1. 校验扩展名、文件大小与空内容；MarkItDown 只处理内存字节流，不访问用户路径或 URL。
2. 转换为 Markdown，执行格式相关的保守清洗和标题感知切分。
3. 保存原始字节，生成带 `SourceInfo` 的统一 Document，将确定性 Node 写入 DocStore。
4. 从当前发布清单生成候选文档集；第一次 P1 发布会先把 P0 Chroma 节点迁入 DocStore。
5. 仅从 DocStore 读取全部 Node，全量重新 embedding 并写入新影子 collection。
6. 写入前后校验模型、provider、归一化策略、向量维度、Node 数量和 Node ID 集合哈希。
7. API 开启 MySQL 事务，写入文档指针后才原子发布影子版本。
8. 发布失败则 MySQL 回滚；MySQL 最终提交失败则恢复原指针。旧 collection 不删除。

## 最佳实践与难点处理

- **解析不完整**：原文件永远保留，DocStore 保留解析诊断；扫描 PDF 通过预览提示 OCR，不伪造文本。
- **重复 Node**：写入前按 Node ID 去重，冲突且正文/metadata 不同时立即阻断；候选 collection 始终全新创建。
- **模型/维度漂移**：候选模型必须与发布版一致，首批向量实际维度还必须与 collection/chunk 契约一致。
- **跨存储事务**：不尝试伪造分布式 ACID；以不可变候选物+单原子指针+可验证补偿实现可证明的安全发布。
- **并发发布**：发布清单携带基线指针/历史指纹，发布时执行 compare-and-swap，过期候选版不得覆盖新版。
- **历史 P0 原文件缺失**：仅把可确认的旧 Node 迁入 DocStore，显式标记 `legacy_unavailable`；绝不用拼接文本伪装原文件。

## 运维命令

```bash
# 从 DocStore 构建完整影子索引，不改变当前发布版
python3 scripts/rebuild_rag_shadow.py

# 原子发布，并执行 MySQL/文件/DocStore/Chroma 对账
python3 scripts/rebuild_rag_shadow.py --publish

# 发布已单独验收的候选版，不重复调用 embedding
python3 scripts/rebuild_rag_shadow.py --release-id <release_id> --publish

# 日常只读对账仍使用管理端 GET /knowledge/health
```
