# RAG Phase 3 独立入口审查

## 审查结论

Phase 3 不依赖 Cross-Encoder，可以独立规划；但当前不具备安全实施条件。必须先完成人工路由策略、评测集和外部模型调用边界确认。

## 现状

### Query Router

- 当前 `QueryModeRouter` 只返回 `knowledge_base` 或 `general`。
- 内部关键词或越权表达命中正则时进入知识库，其余进入通用模式。
- 没有 `ambiguous` 状态、Intent Classifier 或低置信度处理。
- 没有 `route_reason`、`matched_rules`、`route_confidence`、`route_fallback`。
- `CUDA`、`PyTorch` 等同时具有公开知识和平台内部含义的词会被固定路由到知识库；不含既有关键词的内部口语问题可能被路由到通用模式。两类边界必须由产品标注决定，不能由代码自行猜测。

### Query Rewrite

- 当前 `HistoryAwareQueryTransformer` 只在短句或指代表达中拼接最近用户问题。
- 最终回答已经使用原始 `user_message`，检索使用转换后的 query，具备 original/retrieval 分离的基本边界。
- 当前转换没有独立 trace、超时、错误类型或质量指标。
- 当前不存在模型 Rewrite，因此也不存在模型超时/空输出 fallback 的生产验证。

### Golden Dataset

- Baseline V0 共 210 条，全部 `expected_mode=knowledge_base`。
- 50 条 `semantic_rewrite` 是独立完整问题，没有会话历史。
- 29 条 `follow_up` 仍把主题写在当前问题中，也没有 `history`。
- 缺少 General 负样本、真正 Ambiguous 样本、Pronoun 样本和带历史的 Follow-up 样本。
- 因此当前只能计算全 KB 集合上的表面 Router 命中，无法计算 General Precision、Misroute Rate 或 Rewrite 相对 Recall。

## 最小实施清单

人工门禁完成后，Phase 3 第一版只做：

1. 扩展 Router 返回结构化决策与 trace，但保留现有规则。
2. 明确规则只处理高置信度 KB、高置信度 General 和安全强制 KB；其余为 ambiguous。
3. ambiguous 才调用 Intent Classifier；超时、错误或低置信度时安全回退 KB。
4. Rewrite 只处理已判定为 KB 的指代/追问；失败时使用原始 query。
5. original query 只用于最终回答，retrieval query 只用于检索。
6. 默认关闭 Classifier/Rewrite 模型开关，先离线评测。
7. 只使用人工签署的 Phase 3 数据集验收 Router Accuracy、KB Recall 和 Rewrite Retrieval Recall。

## 需要人工确认

1. 路由安全策略：ambiguous、低置信度和 Classifier 失败是否统一回退 `knowledge_base`。
2. 数据集策略：是否允许 AI 生成候选 General/Ambiguous/Pronoun/Follow-up Set，再由人工逐条审核签署。
3. 模型调用：是否允许把当前问题和最多两个历史用户问题发送到当前 DashScope 服务，用于 Intent Classifier 与 Rewrite 离线评测。
4. 生产边界：第一版是否保持 Classifier/Rewrite 默认关闭，只有离线门禁通过并再次人工确认后才启用。

以上任一项未确认前，不修改 Router 或 Rewrite 生产行为。
