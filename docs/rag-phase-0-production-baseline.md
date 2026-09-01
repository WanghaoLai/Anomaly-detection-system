# RAG 生产化 Phase 0：可重复基线

## 第一性原则

Phase 0 只回答“当前系统是什么、当前质量是什么”，不调整算法、Prompt、模型或
检索参数。基线采集必须只读；没有人工确认的数据不生成虚假分数，也不能标记为
`Baseline Version = V0`。

旧的 `config/rag_p0_contract.json` 用于冻结架构迁移前的接口与行为，本阶段在其上
补充生产基线产物，二者职责不同，均保留。

## 生成候选集与快照

```bash
python3 scripts/rag_phase0_baseline.py \
  --source-pdf "/绝对路径/工业异常检测平台知识库文档.pdf"
```

命令不会访问 DashScope、不会写 Chroma、不会切换 Release。它生成：

- `config/rag_golden_dataset_v0.draft.json`：至少 200 条待人工审核候选题；
- `reports/rag_phase0_v0/baseline_config.yaml`：代码、Prompt、模型和关键参数；
- `reports/rag_phase0_v0/baseline_release_manifest.json`：活动 Release 的只读副本或明确的缺失状态；
- `reports/rag_phase0_v0/baseline_eval.json`：逐题评测占位记录；
- `reports/rag_phase0_v0/baseline_metrics.json`：指标框架与阻塞原因。

`reports/` 是本地运行产物且已被 Git 忽略，避免把内部 Release 元数据意外提交到
公开仓库。

## 人工门禁

在继续端到端评测前，人工必须：

1. 逐题审核候选问题，删除无业务价值或表述含糊的题目；
2. 从已发布 Release 中填写 `allowed_doc_ids`、真实 `expected_evidence` node ID；
3. 填写 `expected_answer_points`、`must_not_include` 与拒答预期；
4. 审核 ACL、Prompt Injection、错误前提和多文档冲突策略；
5. 明确知识库内容是否允许发送给当前 DashScope 服务；
6. 确认 Router、Retrieval、Context、Answer、Security 的业务门槛。

活动 Release 的发布、数据删除、阈值确认和 V0 签署均属于人工决策。完成前四个
文件保持 `V0-PENDING`，质量指标为 `null`，验收状态为失败。这是 fail-closed
行为，不是脚本异常。

## 离线验证

```bash
python3 -m unittest tests.test_rag_phase0_baseline
python3 scripts/check_rag_p0_contract.py
python3 -m unittest tests.test_rag_p0_contract
```

## 人工确认后的真实评测

人工决策记录在 `config/rag_phase0_review_policy.json`。确认活动 Release 与
DashScope 处理边界后运行：

```bash
python3 scripts/evaluate_rag_phase0.py
```

脚本会核对审核时 PDF 哈希与 Active Release 原文件哈希，自动映射真实 Node ID，
然后对全部候选题执行批量 Query Embedding、生产 Dense + BM25 + RRF 检索、Context
Packing 和 Qwen 结构化回答。评测前后会比较活动指针，若期间发生发布切换则拒绝把
结果作为基线。自动结果保持 `V0-CANDIDATE`，直至人工抽查回答并签署 V0。

并发批量生成的完成时间包含队列等待，不能冒充用户请求延迟。运行顺序探针取得
分层样本的真实端到端延迟：

```bash
python3 scripts/measure_rag_phase0_latency.py
```

## V0 签署

2026-08-29，项目负责人确认当前评测结果为 `Baseline Version = V0`。签署记录位于
`config/rag_phase0_signoff.json`。这里的“通过”表示阶段 0 已成功冻结改进前状态，
不表示 Router、拒答、错误前提、多文档冲突或生成稳定性已经达到生产门槛；这些已知
缺口保留在基线指标中，供后续阶段做可量化对比。

签署后，三个生成命令默认拒绝覆盖现有 V0。后续候选基线应使用新的输出目录；只有
明确需要替换签署产物时，才可使用 `--replace-signed-baseline`。
