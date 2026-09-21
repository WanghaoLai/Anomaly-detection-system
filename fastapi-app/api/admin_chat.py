import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from common.auth import get_current_admin
from common.resource_limits import llm_capacity_limiter
from common.result import Result
from common.chat_history import conversation_page, message_page, recent_messages
from models import AdminConversation, AdminMessage
from services import LLMService, ChatService
from services.knowledge_service import knowledge_service
from services.rag.operations import (
    PUBLIC_FAILURE_MESSAGES,
    encode_sse,
    iter_until_disconnected,
)
from settings import AI_CONFIG
from tortoise.transactions import in_transaction


router = APIRouter(
    prefix="/admin/chat",
    dependencies=[Depends(get_current_admin)],
)

# 复用与用户聊天一致的 LLM/RAG 服务，确保管理员助手具备相同能力。
_llm_service = LLMService(
    api_key=AI_CONFIG["dashscope_api_key"],
    model=AI_CONFIG["model"],
)
_chat_service = ChatService(_llm_service, knowledge_service)
logger = logging.getLogger(__name__)


class AdminConversationCreate(BaseModel):
    title: Optional[str] = "新对话"


class AdminMessageRequest(BaseModel):
    # 与用户聊天一致的入口限制：超长消息不得直写数据库并进入 LLM 上下文。
    conversation_id: int = Field(ge=1)
    message: str = Field(min_length=1, max_length=8000)


async def _get_owned_conversation(
    conversation_id: int,
    admin_id: int,
) -> AdminConversation:
    """只返回属于当前管理员的会话，避免泄露其他管理员的会话是否存在。"""
    conversation = await AdminConversation.get_or_none(
        id=conversation_id,
        admin_id=admin_id,
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return conversation


@router.post("/conversation")
async def create_conversation(
    data: AdminConversationCreate,
    current_admin: dict = Depends(get_current_admin),
):
    conversation = await AdminConversation.create(
        admin_id=current_admin["user_id"],
        title=data.title,
    )
    return Result.success({"id": conversation.id, "title": conversation.title})


@router.get("/conversations")
async def get_conversations(
    before_at: datetime | None = Query(default=None, alias="beforeAt"),
    before_id: int | None = Query(default=None, alias="beforeId", gt=0),
    page_size: int | None = Query(default=None, alias="pageSize", ge=1, le=100),
    current_admin: dict = Depends(get_current_admin),
):
    if (before_at is None) != (before_id is None):
        raise HTTPException(status_code=422, detail="会话分页游标不完整")
    page = await conversation_page(
        AdminConversation.filter(admin_id=current_admin["user_id"]),
        before_at=before_at,
        before_id=before_id,
        page_size=page_size or 50,
    )
    page["items"] = [
        {
            "id": conv.id,
            "title": conv.title,
            "created_at": conv.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": conv.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
        }
        for conv in page["items"]
    ]
    # 未传分页参数的旧客户端仍收到数组；服务器对其读取量也限制为 50。
    return Result.success(page if page_size is not None or before_at is not None else page["items"])


@router.get("/messages/{conversation_id}")
async def get_messages(
    conversation_id: int,
    before_id: int | None = Query(default=None, alias="beforeId", gt=0),
    page_size: int = Query(default=50, alias="pageSize", ge=1, le=100),
    current_admin: dict = Depends(get_current_admin),
):
    conversation = await _get_owned_conversation(
        conversation_id,
        current_admin["user_id"],
    )
    page = await message_page(
        AdminMessage,
        conversation_id=conversation.id,
        before_id=before_id,
        page_size=page_size,
    )
    page["items"] = [
        {
            "id": msg.id,
            "role": msg.role,
            "content": msg.content,
            "created_at": msg.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        }
        for msg in page["items"]
    ]
    return Result.success(page)


@router.post("/send")
async def send_message(
    data: AdminMessageRequest,
    request: Request,
    current_admin: dict = Depends(get_current_admin),
):
    conversation = await _get_owned_conversation(
        data.conversation_id,
        current_admin["user_id"],
    )

    await llm_capacity_limiter.acquire()
    try:
        async with in_transaction() as connection:
            user_message = await AdminMessage.create(
                using_db=connection,
                conversation_id=conversation.id,
                role="user",
                content=data.message,
            )
            await AdminConversation.filter(
                id=conversation.id,
                admin_id=current_admin["user_id"],
            ).using_db(connection).update(updated_at=datetime.now(UTC))

        history = await recent_messages(
            AdminMessage,
            conversation_id=conversation.id,
            history_limit=int(AI_CONFIG["max_history"]),
        )
        history_list = [{"role": msg.role, "content": msg.content} for msg in history]
    except BaseException:
        llm_capacity_limiter.release()
        raise
    request_id = str(uuid.uuid4())

    async def generate():
        full_response = ""
        terminal = False
        try:
            events = _chat_service.process_message_events(
                data.message,
                history_list[:-1],
                current_admin["user_id"],
                principal=current_admin,
                audit_context={
                    "_trace_id": request_id,
                    "conversation_type": "admin",
                    "conversation_id": conversation.id,
                    "message_id": user_message.id,
                },
            )
            async for event in iter_until_disconnected(
                events, request.is_disconnected
            ):
                if event.get("type") == "content":
                    chunk = str(event.get("content") or "")
                    full_response += chunk
                    yield encode_sse({"content": chunk}, event="content")
                else:
                    yield encode_sse(event, event="status")

            if not full_response:
                raise RuntimeError("生成完成但回答为空")
            await AdminMessage.create(
                conversation_id=conversation.id,
                role="assistant",
                content=full_response,
            )
            if conversation.title == "新对话":
                title = (
                    data.message[:20] + "..."
                    if len(data.message) > 20 else data.message
                )
                await AdminConversation.filter(
                    id=conversation.id,
                    admin_id=current_admin["user_id"],
                ).update(title=title)
            terminal = True
            yield encode_sse(
                {"status": "completed", "done": True}, event="done"
            )
        except asyncio.CancelledError:
            interrupted = PUBLIC_FAILURE_MESSAGES["stream_disconnected"]
            try:
                await asyncio.shield(AdminMessage.create(
                    conversation_id=conversation.id,
                    role="assistant",
                    content=interrupted,
                ))
            except Exception:
                logger.exception(
                    "记录管理员 SSE 断开状态失败: conversation=%s",
                    conversation.id,
                )
            logger.info(
                "管理员 SSE 客户端断开: conversation=%s status=disconnected",
                conversation.id,
            )
            raise
        except Exception as exc:
            code = str(getattr(exc, "code", "generation_failed"))
            message = PUBLIC_FAILURE_MESSAGES.get(
                code, PUBLIC_FAILURE_MESSAGES["generation_failed"]
            )
            logger.exception(
                "管理员聊天生成失败: conversation=%s code=%s",
                conversation.id,
                code,
            )
            await AdminMessage.create(
                conversation_id=conversation.id,
                role="assistant",
                content=message,
            )
            terminal = True
            yield encode_sse({
                "status": "failed",
                "code": code,
                "message": message,
            }, event="status")
            yield encode_sse({
                "status": "failed",
                "code": code,
                "done": True,
            }, event="done")
        finally:
            llm_capacity_limiter.release()
            if not terminal:
                logger.info(
                    "管理员 SSE 流非正常终止: conversation=%s status=disconnected",
                    conversation.id,
                )

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Request-ID": request_id,
        },
    )


@router.delete("/conversation/{conversation_id}")
async def delete_conversation(
    conversation_id: int,
    current_admin: dict = Depends(get_current_admin),
):
    conversation = await _get_owned_conversation(
        conversation_id,
        current_admin["user_id"],
    )
    await AdminMessage.filter(conversation_id=conversation.id).delete()
    await AdminConversation.filter(
        id=conversation.id,
        admin_id=current_admin["user_id"],
    ).delete()
    return Result.success()
