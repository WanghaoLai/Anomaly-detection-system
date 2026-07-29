"""为管理类表维护从 1 开始的连续展示编号。"""
import uuid


async def ensure_sequential_numbers(model, field_name: str, connection) -> int:
    """锁定并按主键顺序压紧编号，返回当前记录数量。"""
    rows = await (
        model.all()
        .using_db(connection)
        .select_for_update()
        .order_by("id")
    )
    expected = [str(index) for index in range(1, len(rows) + 1)]
    current = [str(getattr(row, field_name)) for row in rows]
    if current == expected:
        return len(rows)

    # 唯一索引下直接交换编号可能冲突，先切换到一次性临时值。
    prefix = uuid.uuid4().hex[:12]
    for row in rows:
        await (
            model.filter(id=row.id)
            .using_db(connection)
            .update(**{field_name: f"{prefix}{row.id}"})
        )
    for index, row in enumerate(rows, start=1):
        await (
            model.filter(id=row.id)
            .using_db(connection)
            .update(**{field_name: str(index)})
        )
    return len(rows)
