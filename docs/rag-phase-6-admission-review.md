# RAG Phase 6 准入与最小改造审查

## 审查结论

当前具备进入 Phase 6 的条件，但不具备直接标记为 `Production Ready` 的条件。

Phase 0 至 Phase 5 的主链路、评测基线、Grounding、Release、OCR/Parsing Quality、
Embedding Cache、Trace、全请求 Deadline 和检索降级均已建立。Phase 5 生产冒烟通过，
但指标仍处于 `observe_only`，尚未累计到既定的至少 7 天且 500 条有效请求。

本轮只完成准入审查与改造清单，不在人工确认前改变上传拒绝规则、安装扫描器、启用
进程隔离或发起外部压力流量。

## 已有能力，禁止重复建设

- 上传入口已有扩展名白名单、20 MiB 大小限制、空文件拒绝和路径净化。
- MarkItDown 使用流式输入，插件默认关闭；OCR 已有超时。
- Prompt 已隔离 `question_untrusted`、`history_untrusted`、`knowledge_context` 和
  `allowed_citations`；Citation 与 Claim 由服务端校验。
- ACL 已具备向量检索下推、BM25 过滤和服务端二次过滤；不得删除第二层 ACL。
- 普通 Trace 只记录 Query SHA-256 和候选 metadata，不记录问题、Chunk、私有文档或
  完整 Prompt 正文。
- BM25 缓存已经绑定 Active Release，Release 变化时重建，不存在单独补建需求。
- 向量存储已经通过接口/适配器隔离。后续 Chroma → Qdrant 应复用该边界，本阶段不做
  Chroma 专属多实例增强，也不提前迁移数据。
- Shadow Collection、Release Manifest、原子切换、候选 Smoke Test 和旧 Release 回滚
  已可用。

## 实际最小缺口

1. 上传入口只信任扩展名，缺少 MIME、Magic Bytes 与扩展名/内容一致性校验。
2. OOXML、EPUB 等 ZIP 容器缺少目录穿越、加密条目、条目数、总解压大小和压缩比门禁。
3. 解析器仍运行在线程中；线程超时不能终止底层解析，不能被视为可靠的 Parser
   Timeout，也没有进程级 CPU/内存隔离。
4. 没有本地恶意文件扫描；扫描器不可用、病毒库过期及命中后的失败策略未定义。
5. 缺少统一的 Phase 6 安全、故障注入、压力与长稳报告。
6. 当前正式知识库对普通用户和管理员均开放，无法用真实文档证明跨租户隔离；需使用
   合成受限 Fixture 验证 `Unauthorized Retrieval/Citation = 0`，不能擅自改变现有 ACL
   产品策略。
7. 尚未获得端到端 DashScope 压测的成本/限流授权，也未完成 2/8/24 小时长稳测试。
8. Phase 5 观测量未达到 500 条，因此 P95/P99 和错误率只能记录，不能设硬 SLO。

## 第一性原则下的最小实施顺序

### Phase 6A：上传预检

- 保持当前扩展名白名单不变，不扩大可上传格式。
- 在解析前验证内容家族；扩展名、MIME、Magic/容器结构不一致时 Fail Closed。
- 对 OOXML/EPUB 容器拒绝路径穿越、加密条目和重复危险路径。
- 建议首轮阈值：最多 5,000 个条目、总解压大小 200 MiB、单条目或总体压缩比不超过
  100:1。现有上传大小限制仍保持 20 MiB。
- 空文本/零 Node 继续拒绝；其他 Parsing Quality 仍只观测，不顺带新增质量拒绝规则。

### Phase 6B：解析进程隔离

- 将文档解析放入独立 Worker Process；禁止用 `asyncio.timeout` 包裹线程后宣称已经
  终止解析。
- 建议 Wall Timeout 为 120 秒。超时、崩溃或资源越限只拒绝当前候选上传，Active
  Release 保持不变。
- Linux 生产目标建议先以 1 GiB 解析进程内存、60 秒 CPU 时间做测试基线；当前 macOS
  首轮只验证进程终止和 Wall Timeout，不虚报不可靠的硬内存隔离能力。
- 资源阈值由测试数据校准，不影响在线问答链路和 RAG 生成参数。

### Phase 6C：本地恶意文件扫描

- 候选版本固定为 ClamAV `1.5.4`，许可证为 GNU GPL v2。
- 只使用本地 `clamd/clamscan`；知识文档不提交到 ClamAV、VirusTotal 或其他外部服务。
- 病毒库可从官方源更新，但更新过程不携带知识文档。
- 命中恶意文件、扫描失败、扫描器不可用时 Fail Closed；建议病毒库超过 24 小时未成功
  更新时拒绝新上传，但不影响 Active Release 和在线问答。
- ClamAV 官方建议服务器/桌面环境至少 3 GiB RAM；部署时应与解析 Worker 分开核算，
  不能把解析进程的 1 GiB 基线误当成扫描器总内存要求。

### Phase 6D：安全与故障矩阵

- 使用合成公开/受限 Fixture 验证 ACL Bypass、Hidden Document 与 Unauthorized
  Citation；不修改正式知识库的统一可见策略。
- 覆盖 Prompt Injection、Invalid Citation、Fake Path、Fake Number、Fake Command 和
  错误前提/多文档冲突。
- 覆盖 DashScope 429/500、Embedding/LLM/Reranker Timeout、Chroma、BM25、OCR、MySQL
  与 Release Build Failure。
- 所有路径复核 Retry、Fallback、Circuit Breaker、Grounding Fail Closed、旧 Release
  保持和日志脱敏。

### Phase 6E：性能与长稳

- 第一轮仅在本地/测试环境执行不新增外部模型流量的组件压力测试：10、30、50 并发，
  记录 P50/P95/P99、错误率、CPU、内存和队列等待。
- 100 并发只在前一档资源与错误率稳定后执行，避免为了覆盖清单制造无效负载。
- 端到端 DashScope 压测会产生外部调用、配额和费用，必须另行人工授权。
- 长稳按 2 小时 → 8 小时 → 24 小时递进；每一档通过后再进入下一档。

### Phase 6F：最终 Production Gate

- 复用已签署 Golden V0 与已接受的 Phase 1 代理指标，不重新定义生成行为。
- Phase 2 Cross-Encoder 继续保持 no-go、默认关闭，RRF 仍为生产排序。
- Security Gate 要求 `Unauthorized Retrieval = 0`、`Unauthorized Citation = 0`，且上传
  恶意样本、注入样本和伪引用均 Fail Closed。
- Reliability/Performance Gate 等待 Phase 5 达到至少 7 天且 500 条有效请求，并完成
  经授权的并发与长稳测试。
- Active Release 发布、生产开关、重启和最终 100% 上线仍需人工确认。

## Qdrant 迁移边界

本阶段只保证安全与运行契约不绑定 Chroma：

- 保留 Vector Store 接口；不得从业务服务直接调用 Chroma 客户端。
- Release Manifest、ACL Filter、Node metadata、Embedding 契约和 Trace 字段继续作为迁移
  契约。
- 上传安全、解析隔离、恶意文件扫描、Grounding 与 ACL 二次校验位于向量库之外，迁移
  Qdrant 时无需重写。
- 不在 Phase 6 安全改造中顺带迁移 Qdrant；迁移应作为独立阶段，使用 Shadow Index、
  双读离线对比、候选 Release Smoke 和人工切换。

## 当前人工门禁

实施改变行为的代码前需要确认：

1. 接受 Phase 6A 上传预检策略及首轮阈值：5,000 条目、200 MiB 总解压大小、100:1
   压缩比；扩展名/MIME/Magic 不一致、加密条目或路径穿越均拒绝。
2. 接受 Phase 6B 独立解析进程与 120 秒 Wall Timeout；Linux 测试基线为 1 GiB 内存、
   60 秒 CPU，失败只拒绝当前候选，Active Release 不变。
3. 接受安装并固定 ClamAV `1.5.4`（GPLv2），知识文件仅本地扫描；命中、扫描失败、
   扫描器不可用或病毒库超过 24 小时未更新时拒绝新上传。
4. 接受首轮仅执行本地/测试环境的 10/30/50 并发与 2 小时长稳；端到端 DashScope、
   100 并发及 8/24 小时测试在执行前再次人工确认。

确认前不修改生产请求行为、不安装 ClamAV、不启动压力流量、不重启后端，也不改变
Active Release、ACL、Prompt、Claim/Faithfulness、排序或向量数据。

## 2026-08-31 实施记录

四项策略已由人工确认，Phase 6A–6E 按最小边界实施：

- 上传解析前新增扩展名、具体 MIME、Magic Bytes 和内容家族一致性校验；通用
  `application/octet-stream` 视为未声明 MIME，不绕过 Magic/结构校验。
- 文本执行 Unicode 可解码性检查；JSON/IPYNB/XML 检查结构；XML 拒绝 DOCTYPE 和
  ENTITY。
- OOXML/EPUB 拒绝路径穿越、绝对路径、重复路径、符号链接、加密条目、错配内容家族，
  并执行 5,000 条目、200 MiB 总解压大小和 100:1 单条目/总体压缩比门禁。
- 上传安全门禁位于 MarkItDown、OCR、DocStore 和 Shadow Release 之前；失败只拒绝
  当前候选，Active Release 保持不变。
- 文档解析改为独立 `spawn` 进程，Wall Timeout 120 秒；超时会 terminate/kill 子进程，
  不再使用无法终止底层解析的线程超时。
- 解析进程并发受 `AI_RAG_INGESTION_CONCURRENCY=1` 信号量约束，防止并发上传同时拉起
  大量解析进程。Linux 目标仍为 1 GiB 内存、60 秒 CPU；当前 macOS 只执行 Wall
  Timeout，不宣称具备 Linux 硬资源门禁。
- ClamAV 1.5.4 使用官方 macOS universal PKG；官方发布元数据 SHA-256 为
  `df7fa753e2f9f67f3bc99b2a40a3be7ef559088c68ad6bdf66b4b5764e965bd6`，独立 GPG
  签名由 Cisco Talos 公钥指纹
  `5BADCA2665EF59DCF8A23D8B707F0DB480836771` 验证通过。
- 系统安装需要 root，未擅自索取管理员密码；改为用户级固定安装
  `/Users/xiaohao/.local/share/clamav/1.5.4`。仅修改用户级 `clamscan/freshclam` 的
  `LC_RPATH` 并执行本机 ad-hoc 签名，应用使用绝对路径，不替换系统二进制。
- 病毒库从官方镜像更新：daily 28109、main 63、bytecode 339；知识文件未上传到外部
  扫描服务。普通样本通过，EICAR 标准测试串被拒绝。
- Active Release 仍为 `032a6213d1f04badb4636d79fb102761`，Collection 仍为
  `knowledge_shadow_032a6213d1f04badb4636d79fb102761`。

验证结果：

- Phase 6 安全定向测试：9 项通过、14 个子用例通过；
- P0 分层契约与 Phase 6 组合：15 项通过、1 项按预期跳过；
- RAG/Knowledge/Chat 全量回归：180 项通过、1 项按预期跳过、22 个子用例通过；
- 应用导入和 OpenAPI 路由构建通过；敏感日志静态扫描未发现新增正文、问题、Prompt 或
  `file_bytes` 输出；
- 本地 10/30/50 并发共 90/90 成功、零外部模型调用、零错误；解析并发固定为 1。
  10/30/50 并发 P95 分别为 8.86s、26.12s、46.43s，50 并发最大 48.80s，低于
  120s Wall Timeout；详细数据见 `reports/rag_phase6_local_load.json`。
- macOS 子进程观测峰值约 1.45 GiB。该值不能证明 Linux RSS，也说明 1 GiB 只应保持
  为 Linux 测试基线，未在 Linux Staging 通过前不得宣称资源门禁完成。
- 2 小时本地长稳已完成：目标 7,200 秒，实际 7,200.99 秒；执行 7,067 次上传
  预检、119 次隔离解析和 12 次 ClamAV 扫描，零错误、无外部模型调用，`passed=true`。
  解析延迟 P50/P95/P99 分别为 1.54s、1.64s、1.72s；详细数据见
  `reports/rag_phase6_local_soak_2h.json`。
- 长稳完成后执行最终静态检查、Python 编译检查和 Phase 6 定向回归：60 项通过、1 项
  按预期跳过、14 个子用例通过；Phase 6E 本地/测试环境门禁通过。

## 生产重启与冒烟记录

- 2026-09-01 人工完成后端重启；启动日志确认应用正常完成 startup，并监听
  `127.0.0.1:9090`，前端在 `127.0.0.1:5174` 正常加载。
- 人工完成普通问答和知识库问答冒烟；后端记录 3 次
  `POST /admin/chat/send`，均返回 HTTP 200。
- 人工使用正式知识库 PDF 完成干净文件预览后取消构建，并使用扩展名与内容家族错配的
  PDF 样本确认安全门禁拒绝；后端记录 2 次 `POST /knowledge/preview`，未出现
  `POST /knowledge/upload`，因此没有进入持久化或发布流程。
- 冒烟后 Active Release 仍为 `032a6213d1f04badb4636d79fb102761`，Collection 仍为
  `knowledge_shadow_032a6213d1f04badb4636d79fb102761`；知识目录未生成新的 Release、
  DocStore 或 Raw Artifact。
- 生产日志只记录请求方法、路由、状态码和既有结构化摘要，未记录用户问题、知识文档
  正文、Chunk、Prompt 或上传字节。
- 冒烟后 Python 编译检查通过；在显式关闭生产 Release Smoke 门禁的隔离测试配置下，
  Phase 6 安全、Phase 1 ingestion 和 Phase 4 ingestion 共 37 项回归全部通过。直接继承
  生产 `.env` 时，9 个旧 Phase 1 临时发布夹具会按预期因缺少 Release Smoke 证明而被
  拒绝；该现象属于测试环境隔离要求，不是生产回归或放宽门禁的理由。

## 当前门禁判断

Phase 6 的本地/测试改造、2 小时长稳、生产重启和生产冒烟均已完成，Phase 6 生产验收
通过。上传安全门禁、ClamAV 和隔离解析保持启用，Active Release 与既有 RAG 行为未
改变。

系统级最终 `Production Ready` 仍需等待 Phase 5 累计至少 7 天且 500 条有效请求的硬
SLO 审查；当前 macOS 结果也不能替代未来 Linux Staging 的 1 GiB/60 秒资源门禁验证。

官方来源：

- ClamAV 1.5.4 Release：<https://github.com/Cisco-Talos/clamav/releases/tag/clamav-1.5.4>
- ClamAV GPG 验证方法：<https://docs.clamav.net/manual/Installing.html>
- ClamAV GPLv2 与资源要求：<https://docs.clamav.net/>
