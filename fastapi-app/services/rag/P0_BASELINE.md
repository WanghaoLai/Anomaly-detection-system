# RAG P0 工程基线

P0 的目标不是改变问答效果，而是让后续 LlamaIndex 迁移具备可比较、可回滚的
起点。当前 API、应用端口、评测集、提示词、关键配置和检索指标均由
`config/rag_p0_contract.json` 冻结。

## 分层边界

| 层 | 当前模块 | 约束 |
| --- | --- | --- |
| 领域端口 | `contracts.py` | 仅标准库，不暴露厂商或框架类型 |
| 应用层 | `ingestion.py`、`retrieval.py`、`generation.py`、`splitters.py` | 只做编排和纯算法，不导入厂商 SDK |
| 适配器 | `loaders.py`、`embeddings.py`、`vector_store.py` | 把外部能力转换为稳定端口 |
| 应用门面 | `KnowledgeService`、`ChatService` | 保持 FastAPI 调用兼容，承担事务和装配 |
| API | `api/knowledge.py`、`api/chat.py`、`api/admin_chat.py` | 不直接调用厂商 SDK |

`KnowledgeService` 当前仍兼任遗留依赖装配入口。P0 不移动成熟代码；后续阶段把
DashScope、Chroma、MarkItDown 和 LlamaIndex 对象创建下沉到适配器/bootstrap，
但必须保持 `contracts.py`、`KnowledgeService` 和 `ChatService` 的公开接口兼容。

## 已冻结行为

- 评测集：40 题，其中语义题 18、精确题 22。
- 索引快照：`text-embedding-v2`、1 个文档、14 个分块。
- Dense Candidate Hit@8：0.975。
- Dense Pipeline Hit@4：0.950。
- Hybrid Hit@4：0.975。
- Dense MRR@8：0.805。
- 候选数 8、最终结果 4、阈值 0.20、上下文预算 2800 Token（2026-08-19 由 1800 提升，配合 rerank final_k=8 提升回答覆盖面）。

完整联网报告在本地 `reports/rag_baseline_p0.json`。`reports/` 为运行产物，不提交；
需要重新建立基线时显式执行评测，并把新报告与 P0 契约比较。

## 每次提交的离线检查

```bash
python3 scripts/check_rag_p0_contract.py
python3 -m unittest tests.test_rag_p0_contract
```

检查内容包括：

1. 评测集哈希、数量和分类不变；
2. RAG 关键运行配置不变；
3. 系统提示词不变；
4. 应用端口的方法签名不变；
5. 知识库、用户聊天、管理员聊天的路由、认证和请求形状不变；
6. 应用层不新增 DashScope、Chroma、MarkItDown、LlamaIndex 直接依赖。

## 本地索引与联网行为检查

```bash
# 不访问网络，只核对本机 Chroma 的模型、维度、文档数和分块数
python3 scripts/check_rag_p0_contract.py --check-index

# 需要 DashScope 网络；生成新报告后执行零退化比较
python3 scripts/evaluate_rag.py --output reports/rag_candidate.json
python3 scripts/check_rag_p0_contract.py --report reports/rag_candidate.json
```

任何有意修改接口、提示词、配置或评测集的变更，都必须先生成候选报告、说明原因，
再显式更新契约；禁止为让测试通过而直接降低基线指标。
