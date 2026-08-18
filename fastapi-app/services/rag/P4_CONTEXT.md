# RAG P4 Context Packing

## 第一性原则

重排 Node 只是候选证据，不能直接拼接到 Prompt。生成模型真正接收的上下文必须
同时满足四个不可妥协的约束：总长度有硬上限、每条证据可追溯、相同信息不重复、
命令等原子信息不可被截成错误内容。P4 使用确定性算法完成这些工作，不调用 LLM
做二次摘要，避免压缩阶段引入事实或引用漂移。

## 核心组件

| 组件 | 职责 |
| --- | --- |
| `ContextPackingPolicy` | 固化总预算、Node 最小/最大软预算和近重复阈值 |
| `ContextPacker` | 查询感知单元选择、块级去重、原子保护和严格装箱 |
| `PackedContextEntry` | 保存 citation、Node ID、来源、章节、位置和实际正文 |
| `PackedContext` | 输出最终文本、精确预算、引用映射和去重/省略统计 |
| `PromptBuilder` | 只把已经验收的 Packed Context 放入用户消息 |

实现位于 `context.py`，不导入 DashScope、Chroma 或 LlamaIndex，属于可独立测试的
应用算法。`NumberedContextBuilder` 保留为 P0 兼容名。

## Token 预算划分

默认硬预算为 `AI_RAG_CONTEXT_TOKENS=1800`，所有字符都计入预算，包括总标题、
`[Kx]`、来源、章节、位置、Node ID、正文和省略标记。

| 预算 | 默认值 | 含义 |
| --- | ---: | --- |
| 总上下文硬上限 | 1800 | 最终结果绝不允许超过 |
| Node 正文准入下限 | 48 | 剩余空间不足时不创建空引用 |
| Node 正文软上限 | 420 | 防止高排名长 Node 垄断上下文 |
| 近重复阈值 | 0.92 | 对长文本执行确定性近重复判断 |

普通正文受 420 Token 软上限约束。围栏代码、行内代码和 Shell 命令允许在剩余硬
预算内借用软上限，但只能完整进入或完整省略，永远不会输出半条命令。省略标记会
在写正文前预留预算。

## Context Packing 流程

1. 接收已经完成 dense/hybrid 重排的 Node，不在 P4 改变 Node 排名。
2. 校验稳定 Node ID；缺失 ID 的遗留调用生成确定性派生 ID。
3. 按围栏代码、段落、句子拆为信息单元，并标记原子命令单元。
4. 对查询提取字面特征，让相关单元优先竞争预算；最终仍按原文顺序输出。
5. 先按 Node ID、完整正文去重，再对跨 Node 重叠的信息单元去重。
6. 为真正进入上下文的 Node 连续分配 `[K1]`、`[K2]`，不产生引用空洞。
7. 渲染来源、章节、位置和 Node ID，反算完整文本 Token；超限视为程序错误并阻断。

去重后互补证据可以分布在不同引用中，例如 K1 保留“设备 ID”，K3 保留“防火墙”；
完整上下文仍可共同支持结论，但相同段落只出现一次。

## 最佳实践与故障处理

- P2 分块负责让命令尽量位于同一 Node；P4 再提供最后一道原子截断保护。
- Token 预算必须覆盖引用元数据，不能只统计正文。
- 引用编号在去重和预算过滤之后生成，`citation_map` 始终是一对一映射。
- 不使用生成模型压缩上下文；离线压缩无法证明忠实度时，确定性摘取更安全。
- Context Packer 异常由聊天门面捕获并降级为无 RAG 普通问答，不向模型发送半成品。
- 日志只记录 query 哈希、Token、引用来源及去重统计，不记录原始用户问题。

## 验收与评测

```bash
python3 -m pytest tests/test_rag_p4_context.py tests/test_rag_p4_evaluation.py -q
python3 scripts/evaluate_rag_context.py
python3 scripts/check_rag_p0_contract.py --check-index
```

Context Precision 使用 Ragas 风格的 Average Precision：逐引用判断是否提供至少一项
有效证据，只在相关引用所在排名累计 `Precision@k`。多项证据的完整覆盖基于整个
Packed Context 计算，避免与跨引用去重冲突。门槛由
`AI_RAG_CONTEXT_PRECISION_TARGET` 配置，默认 `0.70`。

当前 40 问真实评测结果（2026-08-18）：

- Context Precision：`0.8951`，通过 `0.70` 门槛。
- Context Hit Rate：`0.975`，与重排 Node Hit Rate 一致。
- Packing Recall vs Selected：`1.0`。
- 预算合规、引用完整、上下文无重复：均为 `1.0`。
- 最大上下文：`1800 / 1800` Token。

