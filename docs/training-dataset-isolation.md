# 训练数据集选择与产物隔离验收

## 改动目标

- 创建训练任务时展示所有已登记且具有根目录配置的数据集。
- PBAS 根据所选数据集切换类别列表和底层数据加载器。
- 新任务产物按算法、数据集和任务三级目录隔离。
- 历史任务继续使用数据库中已保存的旧目录，清理逻辑兼容旧结构。

## 当前 PBAS 数据集映射

| 系统数据集 | PBAS loader | 默认类别 |
| --- | --- | --- |
| MVTec AD | `mvtec` | `screw` |
| VisA | `visa` | `candle` |
| MPDD | `mpdd` | `bracket_black` |
| BTAD | `btad` | `01` |

## 新产物目录

```text
TRAINING_REMOTE_OUTPUT_ROOT/
└── algorithm-{algorithm_id}-{algorithm_abbreviation}/
    └── dataset-{dataset_id}-{dataset_name}/
        └── {job_no}/
            ├── config.json
            ├── command.json
            ├── manifest.json
            ├── raw.log
            ├── artifacts/
            └── work/
```

PBAS 与四个当前数据集的目录分别为：

```text
/home/adtrainer/training-runs/algorithm-1-PBAS/dataset-1-MVTec_AD/{job_no}
/home/adtrainer/training-runs/algorithm-1-PBAS/dataset-3-VisA/{job_no}
/home/adtrainer/training-runs/algorithm-1-PBAS/dataset-4-MPDD/{job_no}
/home/adtrainer/training-runs/algorithm-1-PBAS/dataset-5-BTAD/{job_no}
```

## 人工验收步骤

1. 重启 FastAPI，使新的 Runner 路径生效。
2. 重新构建或启动 Vue 页面，打开“训练任务”并点击“创建训练任务”。
3. 确认数据集下拉框依次包含 MVTec AD、VisA、MPDD、BTAD。
4. 逐个切换数据集，确认类别默认值分别为 `screw`、`candle`、`bracket_black`、`01`。
5. 每个数据集创建一个 1 epoch 测试任务；同一时间可只运行一个，降低 GPU 干扰。
6. 确认任务详情中的算法、数据集、日志、指标和产物正确。
7. 确认 `remoteRunDir` 符合算法/数据集/任务三级目录。
8. 下载一个产物，确认下载边界仍限制在当前任务目录内。
9. 对一个新任务执行产物清理，确认只删除该任务目录。
10. 对一个历史任务执行详情刷新，确认旧的 `{output_root}/{job_no}` 路径仍可读取。

人工验收通过前，不删除服务器上的旧版 Runner：

```text
/home/adtrainer/bin/phase0_pbas_runner.py
```

本次候选版本：

```text
/home/adtrainer/bin/phase0_pbas_runner_dataset_v2.py
```
