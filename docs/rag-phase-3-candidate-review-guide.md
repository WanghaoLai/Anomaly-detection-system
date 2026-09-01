# Phase 3 候选集人工审核说明

候选集：`config/rag_phase3_candidate_v1.json`

本轮只审核数据，不执行 DashScope 评测，不修改 Router/Rewrite 生产行为。

## 审核范围

共 80 条：

- General：20 条高置信公开常识问题。
- Ambiguous：20 条，10 条应进入 General，10 条应进入 Knowledge Base。
- Pronoun：20 条，当前问题依赖一条历史用户问题才能确定主题。
- Follow-up：20 条，当前问题依赖一条历史用户问题完成检索改写。

## 每条必审字段

1. `expected_mode`：目标路由是否正确。
2. `expected_route_stage`：应由明确规则处理，还是交给 Intent Classifier。
3. `expected_retrieval_query`：是否忠实保留当前问题，并正确补全历史主题。
4. `expected_evidence`：知识库问题的 Evidence 是否适合该问题；General 应为空。
5. 问题文字是否自然、无明显提示答案或路由标签泄漏。

审核后把该 Case 的：

```json
"review": {
  "status": "approved",
  "route_label_approved": true,
  "rewrite_target_approved": true,
  "evidence_approved": true,
  "notes": ""
}
```

如需修改问题、label、rewrite target 或 Evidence，应先修改对应字段，再标记 approved。

## 整体签署条件

- 80 条全部审核完成。
- 无重复 ID。
- General Evidence 为空。
- Knowledge Base 的 Ambiguous/Pronoun/Follow-up 均绑定已签署 V0 Evidence。
- 最终将顶层 `status` 改为 `signed_phase3_dataset`，并记录审核人与审核时间。

签署前不会实施 Phase 3 行为代码或调用 DashScope 进行 Classifier/Rewrite 评测。
