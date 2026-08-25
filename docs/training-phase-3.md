# 算法训练升级：阶段 3 验收

阶段 3 在阶段 2 的训练任务管理之上，交付远程日志增量采集、过程指标解析、训练进度、SSE 实时推送，以及前端实时日志和曲线。训练启动、停止、重试、权限和 GPU 租约仍沿用前两阶段的受控执行边界。

## 数据模型

迁移文件：`fastapi-app/migrations/005_training_monitoring.sql`

`training_jobs` 新增：

- `progress_percent`：0～100 的 epoch 进度
- `current_epoch` / `total_epochs`：已完成轮次与计划轮次
- `log_offset`：远程 `raw.log` 的持久化字节游标

新表 `training_logs`：

- 任务内连续 `sequence`
- 日志类型 `STDOUT`、`PROGRESS` 或 `ERROR`
- 清洗后的日志内容
- 对应远程字节偏移量与入库时间

日志游标和内容都在数据库持久化，因此关闭浏览器或重启后端不会丢失已经采集的日志，也不会从头重复导入。

## 增量采集与 PBAS 解析

后端监控周期从 5 秒调整为 2 秒。每次只从已保存的字节游标继续读取远程 `raw.log`，单次最多处理 8 个 512 KiB 分块，并保留不完整的最后一行供下一轮读取。

解析器按实际 PBAS 输出识别：

- epoch 和总 epoch
- segmentation loss
- binary loss
- 每轮 image AUROC
- 每轮 pixel AUROC
- 最终 image/pixel AUROC、AP、PRO 和 best epoch

PBAS 使用回车符不断重绘 batch 进度。系统不会把每次重绘都写入数据库，而是每个同步周期只保留最新训练行；每轮评估结果和最终指标完整保留。ANSI 控制字符、空字符和退格符会在入库前清理，单行最多保留 4000 字符。

过程指标名称：

- `train/segmentation_loss`
- `train/binary_loss`
- `eval/image_auroc`
- `eval/pixel_auroc`

最终指标继续使用阶段 1 的名称，不会覆盖过程曲线。

## SSE 实时接口

接口：

```text
GET /training/jobs/{id}/stream
Content-Type: text/event-stream
```

认证继续使用现有 HttpOnly Access Cookie。普通用户只能订阅自己的任务，管理员可以订阅全部任务。

事件类型：

- `snapshot`：初始任务状态、最近 300 行日志、事件与指标
- `log`：新增的一条持久化日志，包含 SSE `id`
- `state`：状态、进度、事件或指标变化
- `done`：任务进入终态，前端随后主动关闭连接
- `: keepalive`：空闲 15 秒时保持连接

客户端可使用 `afterLogId` 恢复指定日志游标之后的内容。响应禁止代理缓冲和缓存。

## 前端实时监控

打开任务详情后默认进入“实时监控”页签：

- 训练状态、GPU、进度条和当前 epoch
- image/pixel AUROC 每轮曲线
- segmentation/binary loss 每轮曲线
- 最多保留最近 500 行的深色实时日志控制台
- 连接状态：连接中、实时连接、重连中或已结束

页面仍每 5 秒校准任务详情，SSE 用于秒级增量更新。任务结束后收到 `done`，连接自动关闭，最终指标和产物仍可在原页签查看。

## 启动竞态修复

真实短任务验收发现，数据库使用本地时区，而 Python 启动宽限曾使用 UTC 时间对象相减，可能把刚进入 `STARTING` 的任务误判为启动超时。现已改为数据库侧：

```sql
TIMESTAMPDIFF(SECOND, updated_at, NOW(6))
```

启动成功写入 `RUNNING` 时也会清除旧的失败原因、完成时间和退出码。修复后任务 23 创建响应直接为 `RUNNING`，失败原因与完成时间均为空。

## 自动与真实验收记录

自动验证：

- 14 项 Python 单元测试通过
- Python 模块编译检查通过
- Vue 生产构建通过
- 日志解析测试覆盖完成 epoch、batch 重绘抑制、最终指标百分比转换

历史任务回填：

- 任务 ID：`21`
- 状态：`SUCCEEDED`
- 首次打开详情后，从远程日志回填 19 条关键日志
- 恢复 5 个 epoch 的两条 AUROC 曲线和两条损失曲线
- 页面显示 100% 进度、25 项指标和 92 个产物

真实 SSE 任务：

- 任务 ID / 编号：`23 / 66603f82-b1bc-4d86-89eb-c51332536209`
- 配置：PBAS、MVTec AD toothbrush、5 epoch、GPU 2
- 页面运行中观测：`RUNNING / 60% / epoch 3/5 / 实时连接 / 19 行日志`
- 随后观测：`100% / epoch 5/5 / 25 行日志`
- 最终：`SUCCEEDED / exit_code=0 / 已结束 / 26 行日志`
- 数据库：每种过程指标各 5 个 epoch，共 20 个过程指标；5 项最终指标
- 产物：92 项
- 远程复查：`adtrainer` 账号下不存在该任务的 worker 或 PBAS 子进程
- 浏览器控制台：无 warning 或 error

任务 22 用于复现启动时区竞态，实际训练成功、退出码为 0；修复后已清除错误的失败原因。

## 人工验收清单

- [ ] 打开已完成任务详情，“实时监控”能回放历史日志和曲线
- [ ] 新建一个短任务，详情连接状态变为“实时连接”
- [ ] 训练中日志持续增加，关闭并重新打开详情后日志不丢失、不从 1 重复
- [ ] 每轮结束后 AUROC 和损失曲线增加一个数据点
- [ ] 进度条和 epoch 同步增长，成功时达到 100%
- [ ] 任务结束后连接状态自动变为“已结束”
- [ ] 最终指标与过程指标同时存在，过程曲线不会被最终采集覆盖
- [ ] 普通用户不能订阅其他用户任务的 SSE
- [ ] 后端重启后，再打开运行中或已完成任务仍能从持久化游标继续
- [ ] 日志中 ANSI 颜色/进度控制符已清理，长日志不会撑破页面
- [ ] 训练结束或停止后远程进程组全部释放

阶段 3 人工验收通过前，不进入下一阶段。
