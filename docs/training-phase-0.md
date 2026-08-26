# 算法训练升级：阶段 0 验收

阶段 0 只验证 `PBAS + MVTec AD + 单 GPU` 的真实训练链路，不新增训练页面、任务表或通用调度能力。人工验收通过前，不进入阶段 1。

## 已固定的服务器事实

- PBAS 源码：`/home/user/Model-list/PBAS`
- 真实训练入口：`main.py`
- Conda Python：`/home/user/miniconda3/envs/pbas/bin/python`
- Python / PyTorch / CUDA：`3.9.15 / 2.1.2+cu121 / 12.1`
- MVTec AD：`/home/user/Model-list/Datasets/mvtec_ad`，15 个类别目录齐全
- 首次测试：`screw`、5 epochs、单 GPU
- 输出根目录：`/home/user/Model-list/training-runs/phase0`

数据库当前登记的 PBAS 训练入口 `pbas\train.py` 与服务器源码不一致。阶段 0 以只读核验得到的 `main.py` 为准；数据库修正随阶段 0 人工验收结果一起确认。

## 安全边界

`scripts/phase0_pbas_runner.py` 只接受 PBAS、MVTec AD、官方 15 个类别和 1–10 epochs。它使用固定参数列表直接调用 Conda 环境中的 Python，不执行配置中的 Shell 命令，不使用 `conda activate`，也不覆盖已有运行目录。

物理 GPU 通过 `CUDA_VISIBLE_DEVICES` 隔离，PBAS 进程内固定使用 `cuda:0`。算法源码中写死的相对 `results/` 会落入本次任务的独立 `work/` 目录，不会覆盖 PBAS 源码目录里的历史结果。

## 服务器执行

先做只读检查：

```bash
/home/user/miniconda3/envs/pbas/bin/python phase0_pbas_runner.py \
  --config phase0-pbas-mvtec.json \
  --check
```

检查通过后执行：

```bash
/home/user/miniconda3/envs/pbas/bin/python phase0_pbas_runner.py \
  --config phase0-pbas-mvtec.json \
  --run
```

## 输出约定

每次训练生成唯一目录，至少包含：

- `config.json`：本次固定配置快照
- `command.json`：实际 argv，不含 Shell 字符串
- `raw.log`：stdout/stderr 原始日志
- `manifest.json`：状态、开始/结束时间、退出码、产物索引
- `artifacts/`：PBAS checkpoint 和 `results.csv`
- `work/results/`：PBAS 源码使用相对路径生成的训练/评估可视化

成功退出码为 `0`；非零退出码对应 `manifest.json` 中的 `FAILED`。

## 人工验收清单

- [ ] `--check` 显示 PBAS、Conda Python、MVTec 类别、GPU 和 epochs 均正确
- [ ] 训练期间 `nvidia-smi` 可看到只占用配置指定的单张 GPU
- [ ] `raw.log` 能看到 5 个 epoch 及每轮 IAUC/PAUC
- [ ] `manifest.json` 最终为 `SUCCEEDED` 且 `exit_code` 为 `0`
- [ ] `artifacts/models/` 中存在 `center.pth` 和最佳 checkpoint
- [ ] `artifacts/results.csv` 存在并包含 image/pixel AUROC 等指标
- [ ] 刷新或关闭现有 Web 页面不影响服务器训练进程

人工验收完成后记录运行目录、退出码、image AUROC、pixel AUROC 和发现的问题，再决定是否进入阶段 1。

## 首次真实运行记录（待人工确认）

- 运行编号：`pbas-mvtec-phase0-20260729`
- 运行目录：`/home/user/Model-list/training-runs/phase0/pbas-mvtec-phase0-20260729`
- 时间：2026-07-29 10:16:11 至 10:23:27（Asia/Shanghai）
- 状态 / 退出码：`SUCCEEDED / 0`
- 最佳 epoch：3（从 0 开始计数，即第 4 轮）
- image AUROC / AP：`85.18% / 94.44%`
- pixel AUROC / AP / PRO：`97.64% / 14.73% / 91.36%`
- checkpoint：`artifacts/models/backbone_0/mvtec_screw/ckpt_best_3.pth`
- center：`artifacts/models/backbone_0/mvtec_screw/center.pth`
- 隔离可视化：`work/results/` 下共 320 个文件

自动检查已确认日志、指标 CSV、checkpoint、中心特征和隔离可视化均已落盘。仍需人工查看服务器目录和日志，并决定是否把数据库中的错误入口 `pbas\train.py` 修正为 `main.py`。
