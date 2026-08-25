# 训练—推理—实验结果可视化闭环

## 第一性原理

实验结果页面解决的是“结果发现、读取和保存”，不是再制造一份结果副本。系统因此只保留两类事实源：

- 训练任务：远程训练目录中的图片文件，数据库以
  `training_artifacts.artifact_role=EVALUATION_VISUALIZATION` 建立索引。
- 推理任务：远程推理目录中的图片文件，`result_json.visualizationItems`
  保存相对路径和大小；兼容旧任务的 `visualizations` 字符串列表。

统一结果服务只把这两类索引投影成相同的页面协议。算法、数据集、类别、来源任务和完成时间均从原任务读取，避免元数据漂移。

## 远程存储布局

```text
training-runs/
  algorithm-{algorithm_id}-{algorithm_key}/
    dataset-{dataset_id}-{dataset_name}/
      {training_job_uuid}/

inference-runs/
  algorithm-{algorithm_id}-{algorithm_key}/
    dataset-{dataset_id}-{dataset_name}/
      {inference_job_uuid}/
        artifacts/        # 仅 checkpoint 及本次测试生成的指标
        work/results/     # 本次推理生成的可视化图片
        manifest.json
        result.json
```

PBAS 推理只从训练目录复制 `.pth/.pt/.ckpt` 模型文件，不复制训练图片、日志或历史指标。这样既保留任务隔离，又避免每次推理重复存放整套训练结果。

## API

- `GET /experiment-results/options`：当前用户有权访问的算法和数据集筛选项。
- `GET /experiment-results/runs`：合并后的训练/推理结果批次。
- `GET /experiment-results/runs/{source}/{id}/images`：分页图片清单。
- `GET /experiment-results/runs/{source}/{id}/images/{key}`：受控图片预览。
- `GET /experiment-results/runs/{source}/{id}/images/{key}/download`：单图保存。
- `GET /experiment-results/runs/{source}/{id}/download`：全部图片动态打包 ZIP。

所有接口复用任务所有权校验。远程路径必须位于对应任务目录内；批量打包限制 1000 张、单文件 30 MiB、总原始大小 500 MiB。ZIP 使用可回收的临时缓冲生成，不在服务器永久保存第四份副本。

## 新算法与新数据集扩展

新训练适配器只需继续把图片描述为 `EVALUATION_VISUALIZATION`。新推理 runner 在终态结果中输出：

```json
{
  "visualizations": ["work/results/example.png"],
  "visualizationItems": [
    {"path": "work/results/example.png", "sizeBytes": 1024}
  ]
}
```

算法与数据集目录由数据库 ID 加安全化名称生成，因此重名、改名和特殊字符不会造成目录冲突。结果页面无需为新算法增加条件分支。

## 真实验收

- 用户 1 的结果中心正确合并 2 个训练结果批次和 2 个推理结果批次。
- 训练任务 23：读取 84 张 PNG，ZIP 包含 84 个条目。
- 优化前推理任务 2：读取 42 张 PNG，ZIP 包含 42 个条目。
- 优化后推理任务 3：目录为
  `algorithm-1-PBAS/dataset-1-MVTec_AD/{job_uuid}`，推理成功并生成 42 个结构化图片条目。
- 优化后目录没有复制训练图片；`artifacts` 中只有模型文件和本次 PBAS 测试新生成的指标/TensorBoard 文件。
- Python 自动测试、模块编译、FastAPI 导入和 Vue 生产构建均通过。
