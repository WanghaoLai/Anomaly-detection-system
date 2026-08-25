"""为管理类表分配单调递增的展示编号。

编号只增不减：删除不触发全表重排——历史编号是任务记录与外部沟通中的
稳定标识，连续性只是展示需求，由前端列表序号承担。
"""


async def next_sequential_number(model, field_name: str, connection) -> int:
    """返回 max(现有编号)+1；跳过无法解析为整数的非默认值。"""
    values = await (
        model.all()
        .using_db(connection)
        .values_list(field_name, flat=True)
    )
    maximum = 0
    for value in values:
        try:
            maximum = max(maximum, int(str(value)))
        except (TypeError, ValueError):
            continue
    return maximum + 1
