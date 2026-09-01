# RAG Phase 3 实施与离线门禁记录

## 实施范围

- 结构化 Router Trace：`route_mode`、`route_reason`、`matched_rules`、
  `route_confidence`、`route_fallback`、`route_stage` 和耗时。
- 明确规则优先；只有 ambiguous 调用 Intent Classifier。
- Classifier 低置信度、超时、失败统一回退 `knowledge_base`。
- 仅知识库模式使用 Query Rewrite；最多发送两个历史用户问题；失败使用原问题。
- original query 继续用于回答，retrieval query 仅用于检索。
- Trace 写入 SSE；知识库检索 Trace 写入既有审计记录；General 路由单独审计。
- `AI_RAG_PHASE3_ROUTER_ENABLED` 与 `AI_RAG_PHASE3_REWRITE_ENABLED`
  默认均为 `false`。

未修改 Phase 1 Grounding 行为、Phase 2 no-go 结论、生产 RRF 排序、活动 Release
或知识库内容。

## 人工签署输入

- 数据集：`config/rag_phase3_candidate_v1.json`
- 状态：`signed_phase3_dataset`
- Case：80 条，全部逐条审核并保留
- 签署指纹：`143f3fc553a5887990d9b18cc4873bd3d31970b9fded077e0ae13d698dd6861d`
- Active Release：`fda85a12df284a61af4a13fd6d50ede8`

## 固定门禁

| 指标 | 门槛 |
|---|---:|
| Router Accuracy | ≥ 0.95 |
| Knowledge Base Recall | ≥ 0.98 |
| General Precision | ≥ 0.95 |
| Classifier Fallback Rate | ≤ 0.05 |
| Rewrite Fallback Rate | ≤ 0.05 |
| Rewrite Recall@10 | 不低于原问题 |
| Rewrite Context Recall | 不低于原问题 |

## 两轮结果

首轮 Router Accuracy 为 0.9625，但 3 条未指明对象的内部流程问题被误判为
General，KB Recall 为 0.94，结论为 no-go。最小修正只明确 Intent Classifier
Prompt 的既有安全边界：问题不自包含、对象未解析，或询问首次接入/登录流程时
进入知识库；没有增加题目特判、降低门槛或修改 Rewrite/RAG 行为。

第二轮全部门禁通过：

| 指标 | 结果 |
|---|---:|
| Router Accuracy | 1.0000 |
| Knowledge Base Recall | 1.0000 |
| General Precision | 1.0000 |
| Classifier Fallback Rate | 0.0000 |
| Classifier P95 | 653.99 ms |
| Rewrite Fallback Rate | 0.0000 |
| Rewrite P95 | 517.37 ms |
| 原问题 Recall@10 | 0.4833 |
| Rewrite Recall@10 | 0.8000 |
| 原问题 Context Recall | 0.3533 |
| Rewrite Context Recall | 0.7100 |

`expected_retrieval_query` 的逐字匹配率不作为门禁：模型可以产生不同但语义等价的
独立检索问题；真实检索 Recall 与 Context Recall 是本阶段的有效性指标。

完整机器可读结果位于 `reports/rag_phase3_offline_evaluation.json`。该目录为本地
评测产物，不纳入 Git。

## 当前结论

状态为 `offline_gates_passed_pending_production_enablement_signoff`。实现和离线门禁
已经完成，但生产 Router、Intent Classifier 与 Query Rewrite 仍默认关闭。必须再次
获得人工确认后才能改变生产开关；启用决策不属于本轮授权范围。

## 灰度启用记录

人工确认按两步顺序进入生产灰度后，第 1 步已将当前环境配置为：

- `AI_RAG_PHASE3_ROUTER_ENABLED=true`
- `AI_RAG_PHASE3_REWRITE_ENABLED=false`

即启用结构化 Router Trace 与 ambiguous Intent Classifier，Query Rewrite 继续关闭。
第 1 步冒烟与审计兼容检查通过前不得进入第 2 步；第 2 步仍需独立人工确认。

后端重启后的线上冒烟已通过：

- 审计 Trace：`1823bd8f-20b3-4e07-9a0a-9492d360c4ec`
- 状态：`completed`，无 `error_code`
- Router：`intent_classifier -> knowledge_base`，置信度 `1.0`
- Rewrite：`original`，`query_changed=false`，未提前启用模型改写
- Release：`fda85a12df284a61af4a13fd6d50ede8`
- 检索：Dense 50、BM25 18、RRF Union 32、Final 8、Packed 6
- Citation Map：6 条

灰度第 1 步验收完成；进入第 2 步前等待人工确认。

人工确认执行灰度第 2 步后，当前环境配置调整为：

- `AI_RAG_PHASE3_ROUTER_ENABLED=true`
- `AI_RAG_PHASE3_REWRITE_ENABLED=true`

重启并完成带历史追问的线上 Trace/审计核验前，第 2 步仍不视为验收完成。

后端重启后的第 2 步线上冒烟已通过：

- 同一管理员会话先发送独立知识库问题，再发送带历史追问
- 第二条 Trace：`81a8751c-48b2-4a9a-b5a0-d6dc93776cc4`
- 状态：`completed`，无 `error_code`
- Router：`intent_classifier -> knowledge_base`，置信度 `0.9`
- Rewrite：`model`，`query_changed=true`，无 fallback
- 实际检索 Query Hash 与原问题 Hash 不同，确认检索使用改写问题
- Release：`fda85a12df284a61af4a13fd6d50ede8`
- 检索：Dense 50、BM25 16、RRF Union 17、Final/Packed 8
- Citation Map：8 条

灰度第 2 步验收完成，Phase 3 Router、Intent Classifier 与 Query Rewrite 已按
两阶段顺序启用并通过线上 Trace/审计核验。
