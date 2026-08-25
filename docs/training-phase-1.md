# 算法训练升级：阶段 1 验收

阶段 1 只交付训练任务持久化、低权限远程执行器、进程停止和后端重启恢复。普通用户训练页面、参数 Schema 表单、排队/重试等属于阶段 2，本阶段不实现。

## 数据模型

迁移文件：`fastapi-app/migrations/003_training_jobs.sql`

- `training_jobs`：状态、配置快照、GPU 租约、远程目录、PID/PGID、退出码和时间
- `training_events`：有序生命周期事件
- `training_metrics`：结构化最终指标
- `training_artifacts`：远程产物路径与大小索引

迁移只新增这四张表，不修改现有业务表。

## 低权限执行边界

- 远程训练账号：`adtrainer`（UID/GID 1002）
- 账号组：仅 `adtrainer`，不属于 `sudo`、`docker`
- SSH 凭据：独立 `adtrainer_ed25519`；原 GPU 监控密钥不能登录该账号
- PBAS 源码、MVTec AD、Conda 环境：只读/可执行，不可写源码
- 可写目录：`/home/adtrainer/training-control` 和 `/home/adtrainer/training-runs`
- 受审计适配器：`/home/adtrainer/bin/phase0_pbas_runner.py`
- 允许 GPU：由 `TRAINING_GPU_ALLOWLIST` 固定
- 算法/Conda/源码/入口和数据集根目录：分别由两个 JSON 白名单固定，并要求与数据库记录完全一致

用户参数只允许类别、epochs、batch size、workers、尺寸、seed、评估间隔和学习率。执行器不接受 Shell 命令、任意入口或任意路径。

## 进程与恢复

后端通过独立 SSH 账号上传固定 `config.json`，使用 `nohup + setsid` 启动适配器。远程训练不依赖 HTTP、浏览器或 SSH 会话持续存在。

任务会持久化：

- launcher/worker PID
- 训练子进程 PID 和 PGID
- 启动时间、远程控制目录和输出目录
- Conda、源码、入口、数据集、GPU 和执行协议快照

FastAPI 启动时会恢复所有活动任务，之后周期性读取 `runtime.json` 和 `manifest.json`。任务结束后导入 `results.csv` 指标并登记 checkpoint、配置、指标和可视化产物。

停止任务时优先向训练子进程组发送 `SIGTERM`，再结束 worker，避免遗留 DataLoader 等子进程。

## 阶段 1 内部验收 API

这些接口仅管理员可用，阶段 2 才会接普通用户页面：

- `GET /training-internal/health`
- `POST /training-internal/jobs`
- `GET /training-internal/jobs/{id}`
- `POST /training-internal/jobs/{id}/stop`

创建示例：

```json
{
  "algorithmId": 1,
  "datasetId": 1,
  "requestedGpu": 2,
  "parameters": {
    "classes": ["screw"],
    "epochs": 5,
    "batch_size": 8,
    "num_workers": 4,
    "resize": 288,
    "image_size": 288,
    "seed": 1,
    "eval_every": 1,
    "learning_rate": 0.0001
  }
}
```

## 人工验收清单

- [ ] 数据库存在四张 `training_*` 表
- [ ] `adtrainer` 仅属于自己的组，不能写 PBAS 源码
- [ ] 创建任务后数据库状态为 `RUNNING`，并记录 GPU、远程目录和 launcher PID
- [ ] 关闭浏览器不影响远程训练
- [ ] 停止 FastAPI 后，`adtrainer` 的 worker 和训练子进程继续存在
- [ ] 重启 FastAPI 后，任务恢复为 `RUNNING`，并补齐 worker PID、process PID/PGID
- [ ] 任务自然结束后为 `SUCCEEDED`、退出码为 0
- [ ] 事件表有创建、GPU 分配、启动和结束事件
- [ ] 指标表包含 image/pixel AUROC 等最终指标
- [ ] 产物表包含 checkpoint 和 `results.csv`
- [ ] 人工停止另一个短任务后，训练进程组及子进程全部释放

阶段 1 人工验收通过前，不进入阶段 2。

## 真实验收运行记录（待人工确认）

成功任务：

- 数据库 ID / 任务编号：`1 / 8970f0bd-acca-4799-8da9-d49b07c12467`
- GPU / 状态 / 退出码：`2 / SUCCEEDED / 0`
- launcher / worker PID：`2630596 / 2630596`
- 训练 PID / PGID：`2630597 / 2630597`
- 后端启动进程退出后，远程进程继续运行
- FastAPI 完成一次关闭、重新启动和恢复，任务仍为 `RUNNING`
- 最终 image AUROC / AP：`89.08% / 96.01%`
- 最终 pixel AUROC / AP / PRO：`98.28% / 21.78% / 92.50%`
- 数据库登记 5 项指标、328 项产物
- 关键产物：`center.pth`、`ckpt_best_3.pth`、`results.csv`
- 生命周期事件：`JOB_CREATED → GPU_ALLOCATED → PROCESS_STARTED → PROCESS_FINISHED`

停止任务：

- 数据库 ID：`2`
- 停止前 worker / process / PGID：`2632466 / 2632467 / 2632467`
- 停止后状态 / 退出码：`STOPPED / -15`
- `ps` 复查 worker 和训练进程均不存在
- 生命周期事件包含 `STOP_REQUESTED` 和 `PROCESS_FINISHED`

自动验收已覆盖数据库迁移、白名单匹配、低权限账号、独立 SSH 密钥、后端重启恢复、指标/产物采集和进程组停止。仍需人工查看数据库记录、服务器目录和日志后确认阶段 1。
