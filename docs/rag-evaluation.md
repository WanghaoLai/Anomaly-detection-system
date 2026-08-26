# RAG 检索评测

本项目继续使用 Chroma，不引入新的向量数据库或 RAG 框架。评测集位于
`config/rag_eval_questions.json`，当前包含 40 条从现有服务器手册整理的问题，覆盖语义
问法和命令、路径、函数名等精确问法。

## 运行

```bash
# 当前 text-embedding-v2 dense 基线 + 内存中的轻量 hybrid 对照
python3 scripts/evaluate_rag.py

# 额外生成 v4 临时向量进行隔离 A/B，不写 Chroma
python3 scripts/evaluate_rag.py --compare-v4

# 评测未发布影子索引，不改在线指针
python3 scripts/evaluate_rag.py --collection knowledge_shadow_<release_id>
```

`--compare-v4` 会将当前 Chroma 分块发送给已配置的 DashScope Embedding
服务。只能在确认这些知识库内容允许发送到该服务后运行。

## 指标与决策

- `dense_candidate_at_8`：候选 8 条中是否包含答案依据。
- `dense_pipeline_at_4`：经阈值过滤、去重后的最终 3～4 条是否命中。
- `hybrid_at_4`：现有 dense 结果与本地字面匹配 RRF 合并后的 Hit@4。
- `v4_raw_at_4`：v4 临时向量的 Hit@4，不与现有 collection 混用。

只有 hybrid 的整体 Hit@4 至少提升 5 个百分点，或精确类问题至少提升
8 个百分点，且整体不退化，才建议上线轻量混合检索。v4 的 Hit@4 至少
提升 5 个百分点，才建议进入独立 collection 灰度；不直接覆盖现有索引。

问题集应随真实问法持续更新，但始终保持 30～50 条且每条都有可校验的
`expected_all` 答案锚点。

## 2026-08-03 基线结果

评测对象为当前 Chroma 的 15 个分块，问题集为 18 条语义问题和 22 条精确问题。
完整结果保存在 `reports/rag_eval_current_vs_v4.json`。

| 方案 | 整体 Hit@4 | 语义类 | 精确类 |
| --- | ---: | ---: | ---: |
| v2 dense，阈值 0.20 | 75.00% | 66.67% | 81.82% |
| v2 dense + 本地字面 RRF | 92.50% | 88.89% | 95.45% |
| v4 raw，内存隔离对照 | 97.50% | 94.44% | 100.00% |

结论：

- dense 阈值从 0.35 调为 0.20；0.20 与 0.15 命中率一致，优先选择更严格的 0.20。
- 上线轻量 hybrid：仍使用 Chroma dense 候选，本地对 Chroma 正文做标识符/中文
  二元组匹配，使用 RRF 合并，不增加新数据库或 RAG 框架。
- v4 达到灰度门槛，但暂不覆盖现有 `knowledge_base`。下一步应建立独立
  Chroma collection，双写新上传文档并对少量流量双路检索，继续观察答案命中率、
  P95 延迟和 Embedding 费用后再切换。
