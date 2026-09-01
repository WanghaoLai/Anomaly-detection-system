# RAG Phase 4 准入与最小改造审查

## 审查结论

Phase 3 已完成离线门禁、两阶段灰度和线上 Trace/审计核验，Phase 4 的技术准入
条件已经满足。人工已确认 Cache、Parsing Quality、后续本地 OCR 和候选 Release Smoke
四项产品决策。Phase 4A 的最小改造与回归已完成；当前 Active Release 未改变。

## 当前证据

- Active Release：`fda85a12df284a61af4a13fd6d50ede8`
- 活动文档：2 份 PDF，共 58 个 Node
- Shadow Manifest 校验：通过
- 主知识 PDF：30 页，原始字符 33,688，清洗字符 32,815，44 个 Node
- 主知识 PDF 文本充分，不需要 OCR
- 第二份历史 PDF 有 14 个 Node，但旧 DocStore 未保存完整解析诊断
- 当前生产排序仍为 RRF，Cross-Encoder 默认关闭

## 已有能力，禁止重复建设

- 原文件、DocStore、Stable Document/Node ID
- 完整 Shadow Collection、Release Manifest、原子发布和回滚
- Node 数量、重复 Node ID、缺失 Node、Manifest Hash 校验
- Embedding 数量、数值类型、NaN/Infinity、零范数和维度校验
- ACL 元数据与 Manifest 一致性校验
- PDF 低文本预览告警

## 准入时的最小缺口

1. Embedding 每次完整 Shadow Build 都重新调用 DashScope，没有持久 Cache。
2. 解析诊断只有预览告警，没有 `parse_status`、`parse_quality_score`、
   `text_coverage`、`ocr_pages` 和正式质量门禁。
3. 没有 OCR Fallback；当前主机也没有可用的 OCR CLI/运行时。
4. Manifest 只有批次数和节点数，没有 cache hit、API 调用、重试和各阶段耗时。
5. 候选 Release 发布前没有绑定 Golden V0 的检索 Smoke Gate。
6. Shadow 校验只抽样检查一条实际向量维度；完整向量异常已在写入前校验，但发布前
   尚未再次对候选 Collection 全量验证。

## 推荐的最小实施顺序

### Phase 4A：先做无 OCR 的成本与质量基础设施

- 在 Artifact Root 下建立本地持久 Embedding Cache。
- Cache 只缓存文档向量，不缓存在线 Query。
- Cache Key 固定包含：provider、model、schema version、dimension、normalized、
  text type、node ID、text SHA256。
- Cache 读取失败、损坏或维度不符时 fail open，重新调用 Embedding；不得阻断现有
  发布，也不得复用可疑向量。
- 增加构建 Metrics 和完整 Shadow 向量校验。
- Parsing Quality 首轮先记录指标；空文本/零 Node 继续硬失败，其他阈值先不改变
  生产行为。

### Phase 4B：再做 OCR 与正式质量门禁

- 只对 PDF 执行本地 Text Coverage Check。
- 正常文本继续走 MarkItDown；Low-text/Scanned PDF 才进入本地 OCR。
- OCR 文本和原文件不发送到新的外部服务。
- OCR 后重新计算质量分；仍不合格则 `quality_failed`，禁止构建候选 Release。

### Phase 4C：候选 Release 检索 Smoke

- 从已签署 Golden V0 固定选择 20 条关键问题。
- 只对候选 Collection 执行检索，不改变 Active Release。
- Expected Evidence 无法命中时禁止发布。
- 发布 Active Release 仍由人工确认，不自动切换。

## 人工决策记录

2026-08-31，人工确认：

1. 接受完整 Cache Key、本地持久 Cache 和异常时重新生成向量。
2. Parsing Quality 首轮仅观测，除空文本/零 Node 外不新增生产拒绝。
3. 允许后续选择并安装固定版本的本地 OCR 工具及中英文模型；文档只在本机处理。
4. 使用已签署 Golden V0 的固定 20 条关键问题作为候选 Release Smoke Set；Active
   Release 发布仍需人工确认。
5. 后续将 Chroma 迁移为 Qdrant，因此本轮新增边界不得依赖具体向量数据库。

## Phase 4A 实施结果

- 新增 SQLite 本地文档 Embedding Cache；Cache Key 覆盖人工确认的全部字段。
- 缓存读取失败、损坏、维度或归一化不符时 fail open，重新生成并修复缓存。
- Query Embedding 不进入缓存。
- Release Manifest 新增 cache hit、实际 API 调用/重试、异常计数及各阶段耗时。
- Parsing Quality 新增 `parse_status`、`parse_quality_score`、`text_coverage`、
  `text_retention`、`ocr_pages` 和观测告警，不改变现有生产准入行为。
- 候选 Collection 在写入后及发布校验前执行全部向量的 ID、数量、维度、有限值和
  零范数检查。
- Cache 与指标位于 Embedding/Index Writer 契约层；完整向量校验由 Writer Adapter
  实现。未来 Qdrant 只需提供对应 Writer/Store Adapter，不需要改动 Cache Key、
  Parsing Quality 或 Release Manifest。

## 验证结果

- Phase 4A/4B/4C 与 Phase 1 Ingestion 定向测试：28/28 通过。
- 全量 RAG 回归：137 项通过，1 项按预期跳过。
- 知识服务回归：23/23 通过。
- Python 语法检查与 `git diff --check`：通过。
- 真实知识 PDF 单页本地 OCR 冒烟：通过，`chi_sim+eng` 输出 833 字节文本；知识
  内容未发送至外部服务，临时文件已清理。

## Phase 4B 当前进度

- 已安装并固定 Tesseract `5.5.3`，Apache-2.0；Homebrew 已 pin。
- 已固定官方 `tessdata_fast 4.1.0` 的 `eng` 与 `chi_sim` 模型，Apache-2.0。
- `eng` SHA-256：
  `7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2`。
- `chi_sim` SHA-256：
  `a5fcb6f0db1e1d6d8522f39db4e848f05984669172e584e8d76b6b3141e1f730`。
- 已实现低文本/扫描 PDF 才进入 OCR；正常文本继续 MarkItDown。
- MarkItDown 失败时允许本地 OCR 恢复；OCR 失败但 MarkItDown 非空时遵循“仅观测”
  决策保留原文本；两者均为空时仍按原规则拒绝。
- 已安装并固定 Poppler `26.08.0`，许可证为
  `GPL-2.0-only OR GPL-3.0-only`；人工已确认接受，Homebrew 已 pin。
- 合成扫描 PDF 完整离线链路通过：低文本检测、Poppler 渲染、Tesseract OCR、
  Parsing Quality 与 Node 生成均成功。
- `.env` 中 OCR 生产开关已准备为启用，后端尚未重启，因此当前进程行为未改变。

## Phase 4C 实施与候选结果

- 固定 20 条 Smoke Set：`config/rag_phase4_release_smoke_v0.json`，绑定 Golden V0
  文件 SHA-256 与问题指纹。
- Smoke 覆盖接入、账户、资源、环境、数据、网络、SSH、数据集、方法、评测、实验、
  故障和知识更新；每题绑定人工签署的 Expected Evidence Node。
- Smoke 证明独立、不可变，并绑定候选 Manifest SHA-256 与 Smoke Set SHA-256。
- 发布硬门禁在证明缺失、指纹变化或低于 20/20 时拒绝切换；仍必须人工调用发布。
- 候选 Release：`032a6213d1f04badb4636d79fb102761`，58 个 Node。
- 首次候选构建：58 个新向量、0 个复用、3 次 DashScope API、0 次重试；完整向量
  校验通过并已填充本地 Cache。
- 候选 Release Smoke：20/20 Expected Evidence 命中，通过。
- `.env` 中 Release Smoke 生产门禁已准备为启用，后端尚未重启。

## 当前边界

当前 Active Release 仍为 `fda85a12df284a61af4a13fd6d50ede8`，58 个 Node；候选
Release 尚未发布。下一人工介入是重启后端，使 OCR 和 Release Smoke 门禁生效；重启
后需执行只读冒烟和门禁核验。候选即使已通过 20/20，也只有在人工明确确认发布后才可
切换 Active Release。

## 重启后生产核验

2026-08-31 人工重启后端后完成只读核验：

- 后端监听 `127.0.0.1:9090`，根端点返回 HTTP 200；管理员健康端点未认证返回 401。
- 后端进程启动时间晚于 `.env` 更新时间，确认新配置已在重启后加载。
- `AI_RAG_OCR_ENABLED=true`，Tesseract、`pdftoppm`、`pdfinfo` 固定路径和模型哈希
  校验通过。
- `AI_RAG_RELEASE_SMOKE_REQUIRED=true`。
- 候选 `032a6213d1f04badb4636d79fb102761` 的 Manifest 与 58 个 Node 校验通过。
- Smoke 证明为 20/20；Manifest SHA-256 与 Smoke Set SHA-256 绑定均有效。
- Active Release 仍为 `fda85a12df284a61af4a13fd6d50ede8`；候选不是 Active。

截至该次核验，Phase 4 技术门禁已全部通过，唯一剩余动作是人工明确确认是否发布
上述候选 Release；当时未取得该确认前未调用发布操作。

## Active Release 发布结果

2026-08-31，人工明确确认发布候选
`032a6213d1f04badb4636d79fb102761`。发布函数在切换前再次完成 Manifest、完整向量、
20/20 Smoke 证明及 SHA-256 绑定校验，随后原子更新 Active 指针。

- 新 Active Release：`032a6213d1f04badb4636d79fb102761`
- Active Collection：`knowledge_shadow_032a6213d1f04badb4636d79fb102761`
- Node 数量：58
- Embedding：DashScope `text-embedding-v2`，1536 维，normalized，契约一致
- Release Smoke：20/20，通过
- 发布后后端根端点：HTTP 200
- 上一 Release：`fda85a12df284a61af4a13fd6d50ede8`，指针与 Collection 保留，
  可用于受控回滚

Phase 4 已完成。OCR 与 Release Smoke 门禁保持启用；后续 Chroma 迁移 Qdrant 时，
复用 Cache、Parsing Quality、Release Smoke 与发布证明，只替换 Vector Store/Writer
Adapter 并重新执行候选门禁，不复用旧向量数据库的发布证明。
