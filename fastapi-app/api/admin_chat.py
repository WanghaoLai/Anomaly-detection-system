import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from common.auth import get_current_admin
from common.result import Result
from models import AdminConversation, AdminMessage
from services import LLMService, ChatService
from services.knowledge_service import knowledge_service
from settings import AI_CONFIG


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


class AdminConversationCreate(BaseModel):
    title: Optional[str] = "新对话"


class AdminMessageRequest(BaseModel):
    conversation_id: int
    message: str


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
    current_admin: dict = Depends(get_current_admin),
):
    conversations = await AdminConversation.filter(
        admin_id=current_admin["user_id"],
    ).order_by("-updated_at")
    result = []
    for conv in conversations:
        result.append({
            "id": conv.id,
            "title": conv.title,
            "created_at": conv.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": conv.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
        })
    return Result.success(result)


@router.get("/messages/{conversation_id}")
async def get_messages(
    conversation_id: int,
    current_admin: dict = Depends(get_current_admin),
):
    conversation = await _get_owned_conversation(
        conversation_id,
        current_admin["user_id"],
    )
    messages = await AdminMessage.filter(
        conversation_id=conversation.id,
    ).order_by("created_at")
    result = []
    for msg in messages:
        result.append({
            "id": msg.id,
            "role": msg.role,
            "content": msg.content,
            "created_at": msg.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        })
    return Result.success(result)


@router.post("/send")
async def send_message(
    data: AdminMessageRequest,
    current_admin: dict = Depends(get_current_admin),
):
    conversation = await _get_owned_conversation(
        data.conversation_id,
        current_admin["user_id"],
    )

    await AdminMessage.create(
        conversation_id=conversation.id,
        role="user",
        content=data.message,
    )

    history = await AdminMessage.filter(
        conversation_id=conversation.id,
    ).order_by("created_at")
    history_list = [{"role": msg.role, "content": msg.content} for msg in history]

    async def generate():
        full_response = ""
        async for chunk in _chat_service.process_message_stream(
            data.message,
            history_list[:-1],
            current_admin["user_id"],
        ):
            full_response += chunk
            yield f"data: {json.dumps({'content': chunk})}\n\n"

        await AdminMessage.create(
            conversation_id=conversation.id,
            role="assistant",
            content=full_response,
        )

        if conversation.title == "新对话":
            title = data.message[:20] + "..." if len(data.message) > 20 else data.message
            await AdminConversation.filter(
                id=conversation.id,
                admin_id=current_admin["user_id"],
            ).update(title=title)

        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
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
