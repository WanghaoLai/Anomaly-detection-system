# RAG Phase 5 准入与最小改造审查

## 审查结论

当前具备继续改造条件，但不应直接进入 Phase 6。实施计划的推荐顺序为
`Phase 0 → 1 → 2 → 5 → 3 → 4 → 6`；历史实施已完成 Phase 3、4，而 Phase 5
只有部分能力，因此本轮应先补完 Phase 5 的可观测性与可靠性门禁。

Phase 4 Active Release 为 `032a6213d1f04badb4636d79fb102761`，58 个 Node，
Embedding 契约一致。全量 RAG 137 项通过（1 项按预期跳过），知识服务 23 项通过。

## 已有能力，禁止重复建设

- MySQL `RagRetrievalTrace` 与异步审计写入/更新。
- 线上已有 39 条真实 Trace。
- Trace 已记录 Release、Embedding、Prompt、Reranker 版本。
- Router/Rewrite Trace、ACL 后文档数、Dense/BM25/Union/Final/Context 数量。
- Query 只记录 SHA-256，不在普通 Trace 中保存完整问题或 Chunk 正文。
- LLM Token Usage、Grounding Claim 统计、Faithfulness、状态和错误码。
- Qwen 单次调用 Timeout、重试和 Circuit Breaker。
- Query Rewrite 失败回退 Original Query。
- BM25 失败回退 Dense，Cross-Encoder 失败/超时回退 RRF。
- Validator 失败拒答；Release 失败保持旧版本；SSE 断开取消上游任务。
- Phase 4 Index Build、Embedding Cache 与 Release Smoke 指标。

## 实际最小缺口

1. 没有覆盖 Router → Retrieval → Generation → Validator 的全请求 Deadline；各阶段
   Timeout 与受控重生成仍可能串联叠加。
2. Dense Embedding 失败会直接进入无知识拒答，尚未实现 BM25-only 降级。
3. Trace ID 在检索完成后才产生，缺少入口生成的独立 `request_id`，SSE 状态也没有
   稳定关联 ID。
4. Knowledge Trace 的耗时目前主要是 `retrieval_total` 与 `llm`；Router/Rewrite 耗时
   位于嵌套 Trace 中，更新时还会覆盖已有 `stage_durations_ms`。
5. 未独立记录 Dense、BM25、RRF、Context、Validator 与 Total Duration。
6. 已有 Trace 可推导指标，但没有只读聚合报告；当前仅 39 个样本，不足以建立可靠的
   P95/P99 硬门禁。

## 第一性原则下的最小设计

### Phase 5A：Trace 完整性

- API 入口生成 `request_id` 与 `trace_id`，贯穿 SSE、检索和审计。
- 不新增问题正文、Prompt 或知识 Chunk 日志。
- Stage Duration 采用合并更新，禁止后续 LLM 更新覆盖 Router/Rewrite/Retrieval 数据。
- 记录 Router、Rewrite、ACL、Dense、BM25、RRF、Reranker、Context、LLM、Validator、
  Total。
- General 与 Knowledge Base 两条路径使用同一 Trace 生命周期。

### Phase 5B：Total Request Deadline

- 推荐 `75s` 总 Deadline，可通过环境变量配置，首轮默认关闭后离线测试。
- 每个阶段取 `min(自身 timeout, remaining_time)`。
- Deadline 到期取消上游；由于系统在 Validator 通过前不发送正文，不会产生半条或
  未验证回答。
- 对外返回稳定错误码 `request_deadline_exceeded`，不暴露内部阶段或私有内容。
- Grounding/ACL/Validator 不允许因 Deadline 被绕过。

### Phase 5C：对称检索降级

- Dense 失败但 BM25 成功：BM25-only → RRF/Reranker/Context → Validator。
- BM25 失败但 Dense 成功：保留现有 Dense-only。
- Dense 与 BM25 均失败或无授权证据：安全拒答。
- ACL 下推与服务端二次 ACL 在所有路径保持不变。
- Cross-Encoder 默认仍关闭；失败继续回退 RRF。

### Phase 5D：指标首轮观测

- 基于现有 Trace 聚合 QPS、P50/P95/P99、错误率、Route Rate、Empty Retrieval、
  Reranker Fallback、Context、Token、Claim/Refusal、Circuit Open 等指标。
- 首轮仅生成管理员只读报告，不新增外部监控依赖，不自动阻断请求。
- 建议累计至少 `7 天且 500 条有效请求` 后，再由人工确认 P95/P99 与错误率 SLO。

## 受影响文件（预计）

- `fastapi-app/services/rag/operations/`：Request Context、Deadline、审计合并更新。
- `fastapi-app/services/rag/search/pipeline.py`：分支并发、阶段耗时与对称降级。
- `fastapi-app/services/chat_service.py`：全请求 Deadline、Validator/Total Trace。
- `fastapi-app/api/chat.py`、`api/admin_chat.py`：入口 ID 与稳定 Deadline 错误状态。
- `fastapi-app/settings.py`、`.env.example`：开关与参数。
- `tests/`：Deadline、降级、Trace 合并、隐私与回归测试。

不修改 Active Release、Golden Dataset、Prompt、Claim/Faithfulness 策略、OCR、
Embedding Cache、向量数据或 Qdrant 迁移边界。

## 当前人工门禁

实施改变行为的代码前需要确认：

1. 是否接受全请求 Deadline `75s`；到期不发布任何未验证正文，返回
   `request_deadline_exceeded`。
2. 是否接受 Dense 失败时 BM25-only，且继续执行二次 ACL、Context 与 Validator；
   两路均失败才拒答。
3. 是否接受指标首轮仅观测，累计至少 `7 天且 500 条有效请求` 后再确认 SLO 硬门禁。

确认前不修改生产开关、不重启后端、不改变当前请求行为。

## 2026-08-31 实施记录

三项策略已由人工确认，Phase 5 最小改造已完成：

- API 入口生成同一 `request_id/trace_id`，通过响应头、SSE 状态和审计记录贯穿请求；
- Trace JSON 改为合并更新，阶段记录不再互相覆盖；
- 记录 Router、Rewrite、ACL、Dense、BM25、RRF、Reranker、Context Packing、LLM、Validator 和 Request Total 耗时；
- `AI_RAG_REQUEST_DEADLINE_SECONDS` 默认 75 秒，超时错误码为 `request_deadline_exceeded`，不发布未经完整校验的回答；
- Dense 失败可降级为经过 ACL 二次过滤、Context Packing 和回答校验的 BM25-only；两路均失败时进入无知识拒答；
- 新增管理员只读接口 `GET /admin/rag-observability/metrics?days=7`，首轮策略固定为 `observe_only`；
- Active Release、Golden Dataset、Prompt、Claim/Faithfulness、OCR、Embedding Cache、向量库均未改变。

验证结果：

- Phase 5 定向测试：3/3 通过；
- RAG 回归：142 项通过，1 项跳过；
- Knowledge 回归：23/23 通过；
- API 导入顺序回归通过；
- 使用当前 7 天历史 Trace 对指标聚合执行只读验证：30 个有效请求，聚合成功；样本量尚未达到 500，因此不具备设置硬 SLO 的条件。

上线前仍需人工重启后端。重启后应执行一次普通问答和一次知识库问答冒烟，并核验响应头 `X-Request-ID`、对应 Trace 的 `request_total` 及各阶段耗时。完成冒烟前不判定 Phase 5 生产验收完成。

## 2026-08-31 生产验收

人工重启后端后完成生产核验：

- `GET /` 返回 200；
- 未认证访问 `GET /admin/rag-observability/metrics?days=7` 返回 401，证明路由已注册且管理员认证边界生效；
- General Trace `dbae123e-4b31-4030-af7f-f0e83da1fd33` 状态为 `completed`，`request_total=1551.7ms`；
- Knowledge Base Trace `d2581b78-7c11-4ec5-982d-210cc5876a9f` 状态为 `completed`，Active Release 为 `032a6213d1f04badb4636d79fb102761`；
- Knowledge Base 明确规则置信度 0.98，Dense/BM25 均成功，RRF 正常，8 个 Node 完成 Context Packing；
- Knowledge Base 各阶段耗时完整：Rewrite `530.13ms`、ACL `5.5ms`、Dense `428.7ms`、BM25 `19.0ms`、RRF `1821.3ms`、Context Packing `38.2ms`、LLM `4009.5ms`、Validator `4.4ms`、Request Total `6907.7ms`；
- 未触发 `request_deadline_exceeded`、检索分支降级或 Reranker fallback；
- Query 仅保留 64 位 SHA-256；候选审计字段不含 `content`，未记录问题原文或知识正文；
- 11/12 条 Claim 通过单条校验，1 条安全丢弃，回答完整度代理为 `0.9167`，行为符合既定 Phase 1 策略。

Phase 5 生产验收通过。指标继续保持 `observe_only`；累计至少 7 天且 500 条有效请求前，不设置硬 SLO，也不因此阻断在线请求。
