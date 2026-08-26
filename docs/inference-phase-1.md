# PBAS 训练—推理闭环

## 第一性原理边界

推理必须回答三个问题：使用哪个确定的模型、对什么确定的数据执行、结果如何被可靠追踪。
因此本阶段不接受数据库中的任意脚本或用户提供的命令，而只接受：

1. 状态为 `SUCCEEDED` 且产物仍为 `RETAINED` 的训练任务；
2. 该训练任务已经训练过的类别；
3. 管理员白名单内的 GPU 和已注册的 PBAS 适配器。

PBAS 官方仓库没有独立单图 CLI。它通过同一个 `main.py` 的
`--test ckpt`（训练）和 `--test test`（checkpoint 测试）切换模式。因此系统的首个推理闭环定义为
“使用训练 checkpoint 对所选数据集类别执行官方测试流程”，而不是猜测一个不存在的单图接口。

## 数据流

```text
成功训练任务
  ├─ config_json：数据集、类别、预处理参数
  ├─ command.json：已真实执行的固定 argv
  └─ artifacts：ckpt_best、center
          │
          ▼
PBAS inference adapter（校验只能选择已训练类别）
          │
          ▼
远程 inference runner
  ├─ 克隆训练 artifacts（永不覆盖源模型）
  ├─ 校验 argv 的 Python/main.py/数据集路径与白名单一致
  ├─ 仅替换 results_path、test 模式和类别子集
  └─ 写 manifest.json / result.json / raw.log
          │
          ▼
inference_jobs + API + 推理页面（状态、指标、定位图）
```

## 部署与验收

1. 执行 `fastapi-app/migrations/008_inference_jobs.sql`。
2. 将 `scripts/phase0_pbas_inference_runner.py` 以只读方式部署到
   `INFERENCE_REMOTE_RUNNER_PATH`。
3. 确保低权限训练账号可读训练产物和数据集，可写推理输出目录。
4. 重启后端，进入“算法推理”，选择一个成功 PBAS 训练任务提交。
5. 验证任务最终为 `SUCCEEDED`，指标表和定位图可查看，训练目录中文件未变化。

## 真实验收记录

- 数据库迁移 `008_inference_jobs.sql` 已应用。
- 推理 runner 已部署到配置的低权限服务器路径。
- 首次冒烟暴露 MySQL `DATETIME` 与应用时区混算导致的误超时，以及 Click
  chained command 的类别参数顺序问题；均已修复并增加回归测试。
- 修复后的任务 `93a64ab6-ef4c-42bb-8113-358b52ee15cf` 使用训练任务 23 的
  PBAS toothbrush checkpoint，在 GPU 0 上以 `exit_code=0` 成功结束。
- 结果包含 image/pixel AUROC、AP、PRO、best epoch 两行汇总指标和 42 张定位图。
- 训练和推理调度现在共享 GPU 租约视图，避免两类任务并发占用同一 GPU。
