# 工业异常检测科研平台（anomaly_detection_system）

> 一站式工业异常检测科研平台：在浏览器中完成算法浏览、数据集管理、GPU 服务器训练与推理调度、实验结果可视化，并内置基于 RAG 的智能问答助手。

## 为什么需要这个平台

工业异常检测算法的研究通常散落在多台 GPU 服务器上：研究者通过 SSH 手动改配置、敲命令、盯终端日志，再用 scp 拷回结果，实验记录靠笔记，复现靠记忆。本平台把这条链路搬进 Web：

- **训练/推理不在本机执行**——后端通过 SSH/SFTP 调度远程 GPU 服务器，本地无需 GPU；
- **实验过程可追溯**——任务状态、日志游标、指标与产物统一登记，远程 run 目录是文件事实源；
- **知识可沉淀**——上传服务器手册等文档构建 RAG 知识库，智能助手基于知识库回答问题。

## 核心功能

| 模块 | 能力 |
|------|------|
| 用户与权限 | 管理员/用户双角色，JWT HttpOnly Cookie 会话 + CSRF 防护，登录限流与防账号枚举 |
| 算法管理 | 算法信息登记与展示；算法以适配器插件接入（当前内置 PBAS），未注册适配器的算法不能进入训练队列 |
| 数据集管理 | 数据集信息登记（仅登记 `root_directory`，真实数据由 GPU 服务器上的算法加载） |
| 训练调度 | 远程 GPU 服务器提交训练任务：排队、并发与配额控制、GPU 显存门槛、日志实时解析、产物保留策略 |
| 推理评估 | 对训练成功的 checkpoint 提交 `--test` 评估任务，产出官方测试集指标 |
| 实验结果可视化 | 训练/推理指标曲线、异常热力图等统一入口，远程 run 目录为准、数据库只存索引 |
| GPU 服务器监控 | 通过只读账号查看 GPU 状态、白名单目录、受信任 Conda 环境 |
| 智能助手 | 通义千问（DashScope）驱动的用户/管理员双入口对话，SSE 流式输出 |
| RAG 知识库 | 文档上传（docx/pdf/pptx/xlsx/md 等）→ 语义分块 → Chroma 向量索引 → 混合检索（向量 + BM25）+ 可选重排 + 溯源引用 |
| 公告与文件 | 站内公告、头像等文件上传（服务端图像解码校验） |

## 系统架构

```
                          ┌─────────────────────────────────┐
                          │        远程 GPU 服务器            │
                          │  PBAS 训练 / 推理（Conda 环境）     │
                          │  MVTec AD 数据集                  │
                          └───────────▲─────────────────────┘
                                      │ SSH / SFTP（asyncssh）
                                      │ config.json 下发 + manifest.json 轮询
┌──────────────┐  HTTP / JSON  ┌──────┴──────────────────────┐  aiomysql   ┌─────────┐
│   Vue 3 前端  │ ────────────> │         FastAPI 后端         │ ──────────> │  MySQL  │
│ Vite + Element│ <──────────── │  Tortoise ORM · 认证 · 限流    │ <────────── │ ad_system│
└──────────────┘  JWT Cookie   │  Chroma + LlamaIndex（RAG）   │             └─────────┘
                               │  DashScope LLM（智能助手）      │
                               └─────────────────────────────┘
```

三条关键设计约定（详见 [docs/](docs/)）：

1. **训练/推理代理执行**：后端 `TrainingExecutorService` / `InferenceExecutorService` 以独立的低权限 SSH 账号连接 GPU 服务器，SFTP 写入 `config.json` 后用 `nohup` 启动 runner 脚本（`scripts/phase0_pbas_runner.py` 训练、`scripts/phase0_pbas_inference_runner.py` 推理），任务终态以远程 `manifest.json` 为事实源，而非进程退出码。
2. **算法适配器模式**：通用生命周期（排队、GPU 租约、日志、清理）与算法差异（参数、指标、产物）分离；新算法只需在 `fastapi-app/services/algorithm_adapters/` 实现插件契约。
3. **文件优先、DB 做索引**：实验结果以远程 run 目录中的文件为准，数据库仅存索引与任务元数据，避免双写不一致。

## 环境要求

| 组件 | 要求 |
|------|------|
| Python | 3.11+（开发实测 3.13；Vercel 部署配置锁定 3.12） |
| Node.js | 18+（含 npm，用于构建 Vue 前端） |
| MySQL | 5.7+ / 8.0，字符集 utf8mb4 |
| DashScope API Key | 可选；智能助手与 RAG 入库必需 |
| 远程 GPU 服务器 | 可选；训练/推理/GPU 监控必需，需要可 SSH 访问的 Linux 账号 |

> 没有 GPU 服务器也能启动系统：用户、算法、数据集、公告、知识库（需 DashScope Key）等功能均可正常使用，训练相关功能在未配置时不可用。

## 快速开始

以下命令在项目根目录执行。macOS/Linux 为例，Windows 请将 `python3` 换成 `python`。

### 1. 准备数据库

```sql
CREATE DATABASE ad_system DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 生成 JWT 密钥（至少 32 字节，缺失或不安全会阻止后端启动）
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
```

编辑 `.env`，至少填写 `MYSQL_*` 与 `JWT_SECRET_KEY`；智能助手相关功能再填写 `DASHSCOPE_API_KEY`。完整配置项见 [.env.example](.env.example) 内注释。

### 3. 安装依赖

```bash
# 后端
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 前端
cd vue && npm install && cd ..
```

### 4. 初始化数据库

```bash
cd fastapi-app
python3 init_db.py               # 按 Tortoise ORM 模型建表
cd ..
```

需要演示数据（默认管理员账号、PBAS 与 MVTec AD 登记信息）时，改为导入根目录的 `ad_system.sql`。增量结构变更见 `fastapi-app/migrations/`（编号 SQL，按序执行）。

### 5. 启动

**方式一：一键脚本（推荐）**

- macOS / Linux：双击或执行 `./异常检测系统.command`
- Windows：双击 `异常检测系统.bat`

脚本会自动探测 `.venv`、检查依赖、从默认端口（后端 9090、前端 5173）向后寻找空闲端口、拉起前后端并打开浏览器，日志写入 `logs/`。`Control+C` 一并停止两个服务。

**方式二：手动启动（两个终端）**

```bash
# 终端 1：后端（默认 http://127.0.0.1:9090）
cd fastapi-app && python3 -m uvicorn main:app --host 127.0.0.1 --port 9090

# 终端 2：前端（默认 http://127.0.0.1:5173）
cd vue && VITE_BASE_URL=http://127.0.0.1:9090 npm run dev
```

> `VITE_BASE_URL` 是前端请求后端的地址；一键脚本会自动注入。若前端端口与 `.env` 中 `CORS_ALLOWED_ORIGINS` 不一致，请同步修改。

### 6. 登录使用

打开 `http://127.0.0.1:5173`：

- 普通用户在登录页自助**注册**；
- 导入了 `ad_system.sql` 的环境可用默认管理员 `admin / admin` 登录（首次登录后密码自动升级为 bcrypt 哈希，请尽快修改）。

### 首个 API 调用（可选验证）

统一响应格式为 `{"code": "200", "msg": "请求成功", "data": ...}`，认证走 Cookie：

```bash
# 登录并保存 Cookie
curl -s -c cookies.txt -X POST http://127.0.0.1:9090/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "admin", "role": "管理员"}'
# => {"code":"200","msg":"请求成功","data":{"user":{...},"csrfToken":"..."}}

# 携带 Cookie 校验会话
curl -s -b cookies.txt http://127.0.0.1:9090/verify
```

写操作（POST/PUT/PATCH/DELETE）需要额外携带响应中返回的 `csrfToken`（请求头 `X-CSRF-Token`）。

## 目录结构

```
anomaly_detection_system/
├── 异常检测系统.command/.bat   # 一键启动脚本（macOS/Linux、Windows）
├── app.py                     # Vercel 部署入口
├── requirements.txt           # 后端依赖（精确锁定版本）
├── pyproject.toml             # 项目元数据与 Vercel 配置
├── ad_system.sql              # 含演示数据的数据库导出
├── .env.example               # 全部配置项模板
├── fastapi-app/               # FastAPI 后端
│   ├── main.py                # 应用入口（启动训练/推理监视器）
│   ├── settings.py            # 配置加载与校验（读 .env）
│   ├── init_db.py             # 建表脚本
│   ├── models.py              # Tortoise ORM 模型
│   ├── migrations/            # 增量结构变更 SQL
│   ├── api/                   # 路由模块（auth/user/admin/dataset/algorithm/
│   │                          #   training/inference/experiment_results/
│   │                          #   knowledge/chat/server/notice/files）
│   ├── services/              # 业务服务
│   │   ├── training_executor_service.py    # 训练调度与监视
│   │   ├── inference_executor_service.py   # 推理调度与监视
│   │   ├── algorithm_adapters/             # 算法适配器插件（base/pbas/registry）
│   │   ├── rag/               # RAG 流水线（分块/索引/检索/重排/溯源生成）
│   │   └── ...                # GPU 监控、日志解析、实验结果、LLM 会话
│   └── chroma_db/             # Chroma 向量库（本地嵌入式）
├── vue/                       # Vue 3 前端
│   └── src/
│       ├── views/manager/     # 页面（训练任务、推理任务、实验结果、
│       │                      #   知识库、智能助手、服务器监控等）
│       ├── router/            # 路由与守卫
│       └── utils/             # axios 封装（自动刷新会话/CSRF）、鉴权
├── scripts/                   # 部署在 GPU 服务器上的 runner 与运维脚本
│   ├── phase0_pbas_runner.py            # PBAS 训练执行器
│   ├── phase0_pbas_inference_runner.py  # PBAS 推理评估执行器
│   └── evaluate_rag*.py                 # RAG 检索评测
├── config/                    # 训练配置与 RAG 评测集
├── docs/                      # 架构与阶段验收文档
└── tests/                     # pytest 测试（适配器/RAG P0–P6/执行器）
```

## 文档指引

[docs/](docs/) 按用途分为三类：

**概念与架构（理解"为什么"）**

- [算法训练适配器](docs/algorithm-adapters.md) —— 适配器插件契约，如何接入新算法
- [训练与数据集隔离](docs/training-dataset-isolation.md) —— 数据集登记与远程加载的边界
- [认证与安全](docs/authentication-security.md) —— JWT 会话、CSRF、限流设计
- [RAG 架构](fastapi-app/services/rag/README.md) —— 分块→索引→检索→生成分层设计（P0–P5 各阶段文档同目录）

**操作指南（完成具体任务）**

- [训练阶段 0–4](docs/training-phase-0.md)：从 PBAS+MVTec AD 单卡链路验证到任务调度、监控、可靠性、管理
- [推理阶段 1](docs/inference-phase-1.md) —— checkpoint 测试集评估
- [实验结果可视化](docs/experiment-results.md) —— 指标与产物读取口径
- [RAG 检索评测](docs/rag-evaluation.md) —— 评测集与基线对比方法

**参考**

- [.env.example](.env.example) —— 全部环境变量及默认值
- `fastapi-app/migrations/` —— 数据库结构演进记录

## 开发与测试

```bash
# 运行测试（覆盖算法适配器、RAG P0–P6、训练/推理执行器等）
python3 -m pytest tests/

# 代码检查
ruff check fastapi-app scripts tests
```

远程 GPU 相关测试不需要真实服务器——执行器逻辑通过模拟 SSH 行为测试。

## 参与贡献

1. 从 `main` 拉取新分支（如 `feat/xxx`、`fix/xxx`）；
2. 改动涉及以下内容时，请同步更新对应文档并保持同一提交：
   - 数据库结构 → 新增 `fastapi-app/migrations/` 编号 SQL；
   - 训练/推理链路 → 更新 `docs/` 对应阶段文档；
   - RAG 流水线 → 更新 `fastapi-app/services/rag/` 内阶段文档；
   - 新增配置项 → 更新 `.env.example` 并加注释；
3. 保证 `pytest tests/` 通过；新功能请附带测试（参考 `tests/` 现有命名）；
4. 提交信息用简洁中文描述变更目的（参考 `git log` 风格）；
5. 发起 Pull Request 到 `main`，等待评审合并。

## 许可证

内部科研项目，暂未声明开源许可证。
