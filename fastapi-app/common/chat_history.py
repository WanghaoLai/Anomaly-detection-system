"""普通用户与管理员对话共用的有界历史查询。"""

from typing import Any


async def message_page(
    model,
    *,
    conversation_id: int,
    before_id: int | None,
    page_size: int,
) -> dict[str, Any]:
    query = model.filter(conversation_id=conversation_id)
    if before_id is not None:
        query = query.filter(id__lt=before_id)
    newest_first = await query.order_by("-id").limit(page_size + 1)
    has_more = len(newest_first) > page_size
    rows = list(reversed(newest_first[:page_size]))
    return {
        "items": rows,
        "hasMore": has_more,
        "nextBeforeId": rows[0].id if has_more and rows else None,
    }


async def recent_messages(
    model,
    *,
    conversation_id: int,
    history_limit: int,
) -> list:
    # 调用方已经写入当前用户消息，因此多取一条并在传给 ChatService 时
    # 去掉最后一条，可精确保留配置数量的既往上下文。
    newest_first = await model.filter(
        conversation_id=conversation_id
    ).order_by("-id").limit(max(1, history_limit) + 1)
    return list(reversed(newest_first))
