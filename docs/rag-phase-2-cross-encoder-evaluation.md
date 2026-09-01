# RAG Phase 2 Cross-Encoder 首轮评测结论

## 评测边界

- Golden Dataset：已签署 Baseline V0，共 210 条，204 条含检索 Evidence。
- Active Release：`fda85a12df284a61af4a13fd6d50ede8`。
- 模型：`BAAI/bge-reranker-base`。
- 固定 Revision：`580465186bcc87f862a9b2f9003d720af2377980`。
- `model.safetensors` SHA-256：`ced967c45fd1902eb92716c9ceeca7c95a936770ea9db611f5a841b926e33fbd`。
- 运行环境：macOS arm64、CPU；PyTorch MPS 不可用。
- Timeout：2 秒，生产默认开关保持关闭。
- 本轮只评估 Retrieval、Context、Latency 和 Fallback；未执行 Answer Accuracy，避免在资源门禁失败后继续产生生成成本。

完整机器可读报告位于 `reports/rag_phase2_retrieval_matrix.json`，Dense 查询批次缓存位于 `reports/rag_phase2_dense_batch.json`。两者为本地评测产物，不纳入 Git。

## E1–E5 结果

| 实验 | Rerank 输入 | Final K | Recall@5 | MRR | nDCG@10 | Context Recall | Context Precision | Rerank P95 | Fallback |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| E1 | OFF | 8 | 0.8260 | 0.7525 | 0.7577 | 0.8717 | 0.1452 | - | 0% |
| E2 | 30 | 8 | 0.8358 | 0.7808 | 0.7719 | 0.8644 | 0.1426 | 2002.32 ms | 88.1% |
| E3 | 50 | 8 | 0.8260 | 0.7525 | 0.7577 | 0.8717 | 0.1452 | 2002.00 ms | 100% |
| E4 | 30 | 6 | 0.8260 | 0.7525 | 0.7577 | 0.8546 | 0.1776 | 2002.26 ms | 100% |
| E5 | 50 | 6 | 0.8260 | 0.7525 | 0.7577 | 0.8546 | 0.1776 | 2001.49 ms | 100% |

E4/E5 的 Context Precision 增长来自 Final K 从 8 降为 6，而不是 Cross-Encoder；两组均为 100% 回退。E2 只有 25/210 条成功完成重排。成功子集的 MRR 和 nDCG 有改善信号，但覆盖率过低，且 Context Recall/Precision 未改善，不能据此上线。

## 门禁结论

| Phase 2 出口 | 结论 | 证据 |
|---|---|---|
| Retrieval Recall 不下降 | 未稳定满足 | E2 Context Recall 下降 0.0073；其余重排组主要或全部回退 |
| Context Precision 明显改善 | 不满足 | E2 下降 0.0026；E4/E5 的增长不是重排贡献 |
| Answer Accuracy 不下降 | 未评估 | 资源与检索前置门禁已经失败 |
| Reranker P95 在 2 秒 SLO 内 | 不满足 | E2–E5 P95 均到达或超过 2 秒，回退率 88.1%–100% |
| Fallback 正常工作 | 满足 | 所有 timeout/busy 均安全返回 RRF，无空结果或主链路异常 |

当前组合 `bge-reranker-base + CPU + 30/50 candidates + 2s` 不具备生产参数审核条件。生产开关必须继续关闭。

## 最小下一步建议

首轮之后已人工批准只对同一固定模型增加 `max_length=256`，保留 E2 的 30 candidates / Final K 8，并完成第二轮复测。

## 第二轮：E2 + max_length=256

| 指标 | E1 | 第二轮 E2 | Delta |
|---|---:|---:|---:|
| Recall@5 | 0.8260 | 0.6381 | -0.1879 |
| MRR | 0.7525 | 0.6805 | -0.0720 |
| nDCG@10 | 0.7577 | 0.6358 | -0.1219 |
| Context Recall | 0.8717 | 0.6773 | -0.1944 |
| Context Precision | 0.1452 | 0.1077 | -0.0375 |
| Rerank P95 | - | 1302.56 ms | 通过 2 秒资源门禁 |
| Fallback | 0% | 0% | 资源稳定 |

第二轮证明 `max_length=256` 可以解决本机 CPU 延迟问题，但会造成不可接受的质量下降，因此不得执行 Answer Accuracy，也不得生产启用。

只读归因显示：58 个知识节点中 44 个超过 256 tokens；29 个 Golden Evidence 节点中 22 个超过 256 tokens。相对 E1 丢失 Top-5 Evidence 的 39 个 Case，其 Evidence 均存在于 RRF 前 30，但被截断后的 Cross-Encoder 降权。因此失败来自排序输入信息丢失，不是召回不足。

当前模型与本机 CPU 无法同时满足足够文本长度和 2 秒 SLO。下一步必须回到人工模型/资源选择，不再试探中间长度或放宽 timeout。

## 第三轮：mMARCO MiniLM + E2

人工批准后，第三轮只替换为：

- 模型：`cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`
- Revision：`1427fd652930e4ba29e8149678df786c240d8825`
- License：Apache-2.0
- `model.safetensors` SHA-256：`5daeca2481a76b5976a2bdc32f0a78532b6716da4f8cd3ff59460ef8d2f359b4`
- max_length：512
- Rerank Input：30
- Final K：8
- Timeout：2 秒

| 指标 | E1 | 第三轮 E2 | Delta |
|---|---:|---:|---:|
| Recall@5 | 0.8260 | 0.8211 | -0.0049 |
| MRR | 0.7525 | 0.7610 | +0.0085 |
| nDCG@10 | 0.7577 | 0.7413 | -0.0164 |
| Context Recall | 0.8717 | 0.8333 | -0.0384 |
| Context Precision | 0.1452 | 0.1390 | -0.0062 |
| Rerank P95 | - | 1409.00 ms | 通过 2 秒资源门禁 |
| Fallback | 0% | 0% | 资源稳定 |

第三轮解决了模型规模与 512-token 输入的延迟冲突，但仍未满足“Retrieval Recall 不下降、Context Precision 明显改善”的 Phase 2 质量出口。按门禁未执行 Answer Accuracy，生产开关继续关闭。

## 当前阶段决策建议

停止继续试探 Cross-Encoder 模型或参数，保留已经验证的可观测性、单槽隔离和 RRF fallback，但不启用生产重排。将 Phase 2 记录为当前数据集与资源条件下的 no-go，由人工决定是否接受该结论并审查后续阶段是否可以独立推进。

人工已接受 Phase 2 no-go：生产继续使用 RRF，Cross-Encoder 默认关闭，不执行 Phase 2 Answer Accuracy；Phase 3 只进行独立入口审查。
