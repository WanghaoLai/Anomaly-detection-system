import asyncio
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from common.auth import get_current_user
from common.result import Result
from models import Conversation, Message
from services import LLMService, ChatService
from services.knowledge_service import knowledge_service
from services.rag.operations import (
    PUBLIC_FAILURE_MESSAGES,
    encode_sse,
    iter_until_disconnected,
)
from settings import AI_CONFIG


async def get_current_chat_user(
    current_user: dict = Depends(get_current_user),
) -> dict:
    # Conversation.user_id 外键指向 User 表。Admin 与 User 的数字 ID
    # 可能重复，因此管理员不能仅凭相同 ID 被视为会话所有者。
    if current_user.get("role") != "用户":
        raise HTTPException(status_code=403, detail="当前账号不能使用用户会话")
    return current_user


router = APIRouter(
    prefix="/chat",
    dependencies=[Depends(get_current_chat_user)],
)

llm_service = LLMService(
    api_key=AI_CONFIG["dashscope_api_key"],
    model=AI_CONFIG["model"]
)
chat_service = ChatService(llm_service, knowledge_service)
logger = logging.getLogger(__name__)


class ConversationCreate(BaseModel):
    title: Optional[str] = "新对话"


class MessageRequest(BaseModel):
    # 超长消息会直写数据库并进入 LLM 上下文，必须在入口拦截。
    conversation_id: int = Field(ge=1)
    message: str = Field(min_length=1, max_length=8000)


async def _get_owned_conversation(conversation_id: int, user_id: int) -> Conversation:
    """只返回属于当前用户的会话，避免泄露其他用户的会话是否存在。"""
    conversation = await Conversation.get_or_none(
        id=conversation_id,
        user_id=user_id,
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return conversation


@router.post("/conversation")
async def create_conversation(
    data: ConversationCreate,
    current_user: dict = Depends(get_current_chat_user),
):
    conversation = await Conversation.create(
        user_id=current_user["user_id"],
        title=data.title
    )
    return Result.success({"id": conversation.id, "title": conversation.title})


@router.get("/conversations")
async def get_conversations(
    current_user: dict = Depends(get_current_chat_user),
):
    conversations = await Conversation.filter(user_id=current_user["user_id"]).order_by("-updated_at")
    result = []
    for conv in conversations:
        result.append({
            "id": conv.id,
            "title": conv.title,
            "created_at": conv.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": conv.updated_at.strftime("%Y-%m-%d %H:%M:%S")
        })
    return Result.success(result)


@router.get("/messages/{conversation_id}")
async def get_messages(
    conversation_id: int,
    current_user: dict = Depends(get_current_chat_user),
):
    conversation = await _get_owned_conversation(
        conversation_id,
        current_user["user_id"],
    )
    messages = await Message.filter(
        conversation_id=conversation.id
    ).order_by("created_at")
    result = []
    for msg in messages:
        result.append({
            "id": msg.id,
            "role": msg.role,
            "content": msg.content,
            "created_at": msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
        })
    return Result.success(result)


@router.post("/send")
async def send_message(
    data: MessageRequest,
    request: Request,
    current_user: dict = Depends(get_current_chat_user),
):
    conversation = await _get_owned_conversation(
        data.conversation_id,
        current_user["user_id"],
    )

    user_message = await Message.create(
        conversation_id=conversation.id,
        role="user",
        content=data.message
    )

    history = await Message.filter(
        conversation_id=conversation.id
    ).order_by("created_at")
    history_list = [{"role": msg.role, "content": msg.content} for msg in history]
    request_id = str(uuid.uuid4())

    async def generate():
        full_response = ""
        terminal = False
        try:
            events = chat_service.process_message_events(
                data.message,
                history_list[:-1],
                current_user["user_id"],
                principal=current_user,
                audit_context={
                    "_trace_id": request_id,
                    "conversation_type": "user",
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
            await Message.create(
                conversation_id=conversation.id,
                role="assistant",
                content=full_response,
            )
            if conversation.title == "新对话":
                title = (
                    data.message[:20] + "..."
                    if len(data.message) > 20 else data.message
                )
                await Conversation.filter(
                    id=conversation.id,
                    user_id=current_user["user_id"],
                ).update(title=title)
            terminal = True
            yield encode_sse(
                {"status": "completed", "done": True}, event="done"
            )
        except asyncio.CancelledError:
            interrupted = PUBLIC_FAILURE_MESSAGES["stream_disconnected"]
            try:
                await asyncio.shield(Message.create(
                    conversation_id=conversation.id,
                    role="assistant",
                    content=interrupted,
                ))
            except Exception:
                logger.exception("记录 SSE 断开状态失败: conversation=%s", conversation.id)
            logger.info("SSE 客户端断开: conversation=%s status=disconnected", conversation.id)
            raise
        except Exception as exc:
            code = str(getattr(exc, "code", "generation_failed"))
            message = PUBLIC_FAILURE_MESSAGES.get(
                code, PUBLIC_FAILURE_MESSAGES["generation_failed"]
            )
            logger.exception(
                "聊天生成失败: conversation=%s code=%s", conversation.id, code
            )
            await Message.create(
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
            if not terminal:
                logger.info(
                    "SSE 流非正常终止: conversation=%s status=disconnected",
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
        }
    )


@router.delete("/conversation/{conversation_id}")
async def delete_conversation(
    conversation_id: int,
    current_user: dict = Depends(get_current_chat_user),
):
    conversation = await _get_owned_conversation(
        conversation_id,
        current_user["user_id"],
    )
    await Message.filter(conversation_id=conversation.id).delete()
    await Conversation.filter(
        id=conversation.id,
        user_id=current_user["user_id"],
    ).delete()
    return Result.success()
