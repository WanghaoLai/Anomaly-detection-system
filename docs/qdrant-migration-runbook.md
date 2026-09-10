# Chroma → Qdrant 个人开发迁移方案

## 1. 目标与边界

当前项目是个人开发项目，只验证 Qdrant 能否在不破坏现有 RAG 的前提下替代 Chroma，暂不建设生产环境。

- Chroma 继续作为默认向量库和可靠回退路径。
- Qdrant Cloud 已被批准作为开发系统的候选向量存储，用于解决本机空间不足；本地合成测试仍可使用嵌入式 local 模式。
- DocStore/Release Manifest 仍是事实源，Qdrant 只保存可重建的派生索引。
- Point ID 使用 Node ID 派生的确定性 UUIDv5，原 Node ID 保存在 payload。
- Active Pointer 决定当前 Release 及 provider，不使用 Qdrant Alias。
- 当前仍是个人开发范围，不承诺 SLA/RTO，也不物理退役 Chroma。

## 2. 目标状态

```text
DocStore / Manifest（事实源）
            |
            +-- Chroma Release（默认、保留）
            |
            +-- Qdrant Cloud Release（候选、可删除重建）

Active Pointer -> 当前开发运行使用的 provider + release
```

默认配置：

```dotenv
AI_VECTOR_STORE_PROVIDER=chroma
AI_QDRANT_MODE=server
AI_QDRANT_URL=https://<cluster-endpoint>
AI_QDRANT_API_KEY=<private-database-api-key>
```

`AI_VECTOR_STORE_PROVIDER` 只决定新候选 Release 写入哪里；在线读取仍由 Active Pointer 决定。

## 3. 开发阶段

### D0：保护现有基线（已完成）

- 记录当前 Chroma Release、文档数、Node 数、Embedding 维度和距离类型。
- 验证旧版 `shadow-release-v1` 指针仍可读取。
- 不改写当前真实 Active Pointer，不删除 Chroma collection。

### D1：本地适配器验证（已完成）

```bash
python3 -m pytest tests/test_rag_qdrant_indexing.py -q
```

必须覆盖：确定性 Point ID、写入/查询、ACL 过滤、向量和 payload 校验、Manifest v1/v2 兼容、Qdrant 发布及回滚。

### D2：合成数据全链路（已完成）

使用临时 DocStore、临时 Chroma 和临时 Qdrant local 目录验证：

1. Chroma 基线构建；
2. 从同一 DocStore 全量构建 Qdrant；
3. 校验数量、Node ID、Point ID、维度、正文和 ACL；
4. 临时切换 Active Pointer；
5. 执行查询；
6. 回滚到 Chroma；
7. 删除临时目录。

自动化测试已经覆盖该路径，不再维持远程 Staging。

### D3：Qdrant Cloud 真实数据影子验证（已授权，等待连接参数）

已授权将真实知识库的分块正文、向量和 ACL payload 上传到指定区域的 Qdrant Cloud 开发集群。真实数据上传前必须先通过只使用合成数据的 Cloud 预检；整个 D3 不自动发布。

#### D3C-1：合成预检

```bash
export AI_QDRANT_URL=https://<cluster-endpoint>
export AI_QDRANT_API_KEY=<database-api-key>
python3 scripts/preflight_qdrant_cloud.py
```

预检只创建随机命名的合成 collection，验证 HTTPS、认证、payload 索引、写入、ACL 过滤与删除，并在 `finally` 中清理 collection。预检失败时禁止上传真实数据。

#### D3C-2：真实影子重建

```bash
export AI_VECTOR_STORE_PROVIDER=qdrant
export AI_QDRANT_MODE=server
export AI_QDRANT_URL=https://<cluster-endpoint>
export AI_QDRANT_API_KEY=<database-api-key>
python3 scripts/rebuild_qdrant_shadow.py
python3 scripts/validate_qdrant_release.py <release_id>
python3 scripts/compare_chroma_qdrant.py <release_id>
python3 scripts/benchmark_chroma_qdrant.py <release_id>
```

执行前必须满足：

- 明确 Cloud 区域、Cluster URL 和集群用途；
- Database API Key 具有预检和迁移所需权限，且不写入仓库、日志或报告；
- 合成 Cloud 预检通过并确认测试 collection 已删除；
- 当前 Active Pointer 的文件哈希已记录；
- 明确只构建候选 Release，不发布。

### D4：开发系统切换到 Qdrant Cloud（已完成）

允许 Qdrant Cloud 作为系统向量存储不等于授权立即切换具体 Release。只有 D3 的数据、Point ID、向量、ACL 和查询验收通过后，才提交包含明确 Release ID、collection 名和回滚指针的人工发布确认。切换后执行开发系统冒烟；发生异常立即恢复原 Chroma 指针。

当前待确认候选：

- Release ID：`2ecaec37b4324301bb04d852aa53e873`
- Collection：`knowledge_shadow_2ecaec37b4324301bb04d852aa53e873`
- 数据校验：58/58 Node、向量和 Point ID 通过
- 质量：参考阈值全部通过，平均 Top-8 重合度 1.0
- 性能观察：Chroma P95 2.276 ms；Cloud P95 655.75 ms，包含 `eu-west-1` 跨区和本机代理延迟
- Release Smoke：固定 Golden V0 20/20 通过
- 当前状态：已发布，Active Pointer 指向 Qdrant Cloud
- 切换后冒烟：Embedding 一致；普通用户/管理员检索成功；空授权和伪造 doc_id 均为 0 命中；在线 chunk 数 58
- 回滚基线：Chroma Release `032a6213d1f04badb4636d79fb102761`，collection 58 Node，已确认仍可读

## 4. 开发验收标准

必须通过：

- Qdrant Node ID 集合与候选 Manifest 完全一致；
- Point ID 与 Node ID 的 UUIDv5 映射一致；
- 向量数量、维度、非零和有限值校验通过；
- 未授权文档不能被检索；
- Manifest/Pointer 可在 Chroma 与 Qdrant 之间切换和回滚；
- 当前旧 Chroma Release 始终可读且不被删除。

用于观察而非阻断个人开发：

- Chroma/Qdrant Top-K 重合度；
- 检索质量指标的轻微差异；
- 本机延迟与资源占用。

数据不完整、权限泄漏或无法回滚时，不允许切换；质量与性能差异由开发者自行决定是否继续实验。

## 5. 回退策略

- 代码默认 provider 保持 `chroma`。
- 候选构建失败时只删除本次 `knowledge_shadow_...` Qdrant collection。
- 本机切换失败时恢复切换前 Active Pointer，并清理进程内 collection/provider 缓存。
- Chroma 依赖、数据目录和 Release 不进入本次迁移的删除范围。
- Qdrant Cloud 数据是可重建派生物；仅删除明确的候选 collection，Cloud API Key 不写入 Manifest。

## 6. 已回退的过度建设

本次方案重设后已从代码库移除：

- 生产 Compose、生产环境模板与生产预检脚本；
- TLS/自定义 CA、管理 Key/只读 Key 双客户端配置；
- 知识库生产切换只读窗口开关；
- 远程 Staging 专用镜像、Compose、合成验收脚本和部署文档；
- 生产 Snapshot 下载/恢复与长期保留工具；
- G1–G8 生产审批、14 天观察期和 30 天 Chroma 退役流程。

这些能力只有项目明确进入上线准备后才重新设计，不能直接把旧模板视为可上线配置。

## 7. 当前停止线

D2、D3-Cloud 和 D4 均已完成。开发系统当前使用 Qdrant Cloud Release `2ecaec37b4324301bb04d852aa53e873`；Chroma Release `032a6213d1f04badb4636d79fb102761` 继续保留为回滚路径，不得物理删除。
