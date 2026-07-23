import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from common.auth import get_current_user
from common.result import Result
from models import Conversation, Message
from services import LLMService, ChatService
from services.knowledge_service import knowledge_service
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


class ConversationCreate(BaseModel):
    title: Optional[str] = "新对话"


class MessageRequest(BaseModel):
    conversation_id: int
    message: str


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
    current_user: dict = Depends(get_current_chat_user),
):
    conversation = await _get_owned_conversation(
        data.conversation_id,
        current_user["user_id"],
    )

    await Message.create(
        conversation_id=conversation.id,
        role="user",
        content=data.message
    )

    history = await Message.filter(
        conversation_id=conversation.id
    ).order_by("created_at")
    history_list = [{"role": msg.role, "content": msg.content} for msg in history]

    async def generate():
        full_response = ""
        async for chunk in chat_service.process_message_stream(data.message, history_list[:-1], current_user["user_id"]):
            full_response += chunk
            yield f"data: {json.dumps({'content': chunk})}\n\n"

        await Message.create(
            conversation_id=conversation.id,
            role="assistant",
            content=full_response
        )

        if conversation.title == "新对话":
            title = data.message[:20] + "..." if len(data.message) > 20 else data.message
            await Conversation.filter(
                id=conversation.id,
                user_id=current_user["user_id"],
            ).update(title=title)

        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
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
