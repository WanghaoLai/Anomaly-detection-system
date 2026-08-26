# 多论文阶段 0：最终 baseline_v1

本文记录 `MULTI_PAPER_RAG_DESIGN.md` 阶段 0 的完整冻结结果。语料、黄金集、
当前 RAG release、全量评测和最终 `baseline_v1` 均已完成。

## 已冻结产物

- `config/rag_multi_paper_corpus_v1.json`：15 篇论文、184 页、78,327,297 字节；
  每篇保存稳定 `document_id`/`work_id`、原 PDF SHA256、字节数、页数、书目信息、
  文本抽取哈希与诊断。
- `config/rag_multi_paper_eval_v1.json`：42 条人工核验问题，覆盖单论文事实、
  跨论文比较、跨论文综合、多轮追问、精确术语、跨语言、表格/图注、负样本、
  权限和冲突结论十类场景。
- `config/rag_multi_paper_baseline_v1_contract.json`：冻结上述数量、分类分布和
  规范化 JSON 哈希。已冻结文件如需修改，必须新建 v2，不得就地放宽契约。
- `config/rag_multi_paper_baseline_v1.json`：冻结 release、运行参数、文档映射、
  对账结果、检索/生成指标、建议门槛判断和已知差距。

## 发布与评测结果

- Release：`b17672e25ed44ee793a8799def2d968e`
- Collection：`knowledge_shadow_b17672e25ed44ee793a8799def2d968e`
- 索引：15 篇论文、974 个节点；MySQL、Raw File、DocStore 与 Chroma 对账 0 问题。
- 检索：Paper Recall@10 `0.9535`、Evidence Recall@20 `0.5673`、
  Document Coverage Recall `0.7792`、MRR `0.9079`、nDCG@10 `0.9041`。
- 安全：负样本通过率 `1.0`、权限绕过拦截率 `1.0`、未授权泄漏率 `0.0`。
- 生成：Citation Validity `0.95`、Claim Faithfulness `0.8251`；40 个生成题中
  2 个因 DashScope 45 秒超时失败。上述结果作为当前真实基线冻结，不视为已达到
  设计文档中的后续候选准入目标。

原 PDF 因体积与授权边界不提交仓库。清单以内容哈希冻结它们，默认源目录为
`/Users/xiaohao/Desktop/杂物/Papers`，其他环境通过命令参数提供源目录。

## 证据与权限约定

黄金记录保留设计文档要求的 `question`、意图、相关 work/document ID、
`required_aspects`、`expected_claims`、`forbidden_claims` 和可信主体。
当前尚未入库，因此使用稳定的页级 `evidence_anchors`；完成入库后可以在候选
评测产物中追加 `relevant_node_ids`，但不能覆盖 v1 页级证据。

权限题使用每题 `corpus_acl_overrides` 构造隔离场景。相关文档只允许包含当前
主体可见的文档；拒绝题保留 `scope_work_ids`，但不把不可见文档标为 relevant。

## 校验

每次提交运行不依赖论文目录的冻结契约测试：

```bash
python3 scripts/check_rag_multi_paper_phase0.py
python3 -m unittest tests.test_rag_multi_paper_phase0
```

在持有原论文的机器上运行完整来源和证据核验：

```bash
python3 scripts/check_rag_multi_paper_phase0.py \
  --source-dir "/Users/xiaohao/Desktop/杂物/Papers" \
  --verify-text
```

`--verify-text` 固定使用 PyPDF2 3.0.1 的逐页抽取规则，复算抽取文本哈希，并检查
可可靠抽取论文的每个 locator term 是否出现在指定页。中文《半监督自训练方法综述》
包含 GBK 字体映射告警，PyPDF2 会稳定地产生乱码，因此该文只自动核对原 PDF 与
抽取结果哈希，语义锚点标记为人工视觉复核，不能用乱码匹配伪装成自动验证。

## 阶段 0 完成状态

```text
冻结论文语料（完成）
  -> 构建黄金评测集（完成）
  -> 当前 RAG 入库（完成）
  -> 全量评测（完成）
  -> 冻结 baseline_v1（完成）
  -> 后续阶段统一对照
```

最终静态校验：

```bash
python3 scripts/check_rag_multi_paper_phase0.py
python3 scripts/check_rag_multi_paper_baseline_v1.py
python3 scripts/check_rag_multi_paper_baseline_v1.py --verify-runtime
python3 -m unittest tests.test_rag_multi_paper_phase0 tests.test_rag_multi_paper_runtime
```
