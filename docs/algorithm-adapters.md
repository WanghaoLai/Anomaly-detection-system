# 算法训练适配器

## 设计边界

训练系统只有两类变化：

- 通用生命周期：任务排队、GPU 租约、SSH 启动、停止、重试、日志游标、审计和清理。
- 算法差异：参数、数据集约束、远程配置、日志语义、最终指标和产物角色。

`TrainingExecutorService` 只处理第一类变化。第二类变化由
`fastapi-app/services/algorithm_adapters/` 下的插件处理。算法没有注册适配器时不会出现在训练选项中，也不能进入训练队列。

PBAS 是首个内置插件，注册键为 `PBAS`。重构保留了原有
`TRAINING_REMOTE_RUNNER_PATH`、远程 JSON 结构、日志解析结果和产物分类，历史任务没有适配器快照时会根据数据库算法缩写回退解析。

## 插件契约

新插件继承 `AlgorithmAdapter`，至少实现：

- `key`：与 `algorithms.abbreviation` 大小写无关地匹配。
- `validate_parameters()`：拒绝未知或越界参数，并补齐默认值。
- `build_remote_config()`：只返回 JSON 可序列化数据，不接受或拼接 shell 命令。

通常还应按算法覆盖：

- `validate_dataset()`：在入队前验证数据集兼容性。
- `parse_log_line()`：输出进度和指标。
- `metric_artifact_paths()` / `extract_final_metrics()`：读取最终指标。
- `describe_artifact()`：赋予 checkpoint、指标和可视化文件明确角色。
- `runner_path()`：算法使用不同远程 runner 时，返回管理员预部署的绝对路径。
- `total_epochs()`：供任务进度初始化使用。
- `supports_inference=True`、`validate_inference_parameters()` 和
  `build_inference_config()`：声明算法已完成推理适配。推理页面只展示具备该能力的算法，
  不按 PBAS 等算法名称硬编码。

注册插件：

```python
from services.algorithm_adapters.registry import algorithm_adapter_registry

algorithm_adapter_registry.register(MyAlgorithmAdapter())
```

内置插件应在 `services/algorithm_adapters/__init__.py` 中显式注册。显式注册使可执行代码来源可审计，避免从数据库路径动态导入任意 Python 模块。

## 远程 runner 协议

适配器返回的 runner 必须支持既有的安全 argv：

```text
python RUNNER --config CONFIG_JSON --run --run-id JOB_UUID
```

runner 不得把用户参数解释成 shell，必须使用固定 argv 启动算法进程。运行目录必须是
`TRAINING_REMOTE_OUTPUT_ROOT/JOB_UUID`，并至少维护：

- `manifest.json`：`status`、`exit_code`、进程信息和 `artifacts`。
- `runtime.json`：worker、算法进程 PID/PGID。
- `raw.log`：UTF-8 训练日志。
- `config.json`、`command.json`：不可变配置和实际 argv 快照。

这组文件是调度器与算法进程之间的稳定边界。新算法无需修改任务 API、数据库状态机、GPU 调度或监控循环。

推理适配器若生成实验图片，应在终态 `result.json` 中同时提供兼容列表
`visualizations` 和带文件大小的 `visualizationItems`。统一实验结果页面只依赖该协议，
不会按算法名称扫描目录或增加硬编码分支。

## 接入检查清单

1. 实现并注册适配器，确保 key 与数据库算法缩写一致。
2. 在数据库配置参数 Schema、资源规格和数据集要求。
3. 用低权限训练账号部署 runner；源码和数据集保持只读，输出目录单独可写。
4. 为参数拒绝、固定 argv、日志样例、指标和产物分类添加单元测试。
5. 用 `--check` 验证远程路径，再执行最小 epoch 冒烟训练。
6. 验证停止、失败、重试、日志同步、产物下载和清理流程。
