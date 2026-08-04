"""知识库管理 API（仅管理员）"""
import logging
import os

from fastapi import APIRouter, Depends, UploadFile, File
from tortoise.transactions import in_transaction

from common.auth import get_current_admin
from common.exception_handler import CustomException
from common.result import Result
from models import Knowledge
from settings import AI_CONFIG
from services.knowledge_service import (
    SUPPORTED_DOCUMENT_EXTENSIONS,
    KnowledgeService,
)

router = APIRouter(prefix="/knowledge", dependencies=[Depends(get_current_admin)])

knowledge_service = KnowledgeService()
logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = SUPPORTED_DOCUMENT_EXTENSIONS
MAX_UPLOAD_BYTES = int(AI_CONFIG.get("rag_max_upload_bytes", 20 * 1024 * 1024))
UPLOAD_READ_CHUNK_BYTES = 1024 * 1024
if MAX_UPLOAD_BYTES <= 0:
    raise RuntimeError("AI_RAG_MAX_UPLOAD_BYTES 必须大于 0")


async def _read_upload_limited(file: UploadFile) -> bytes:
    """分段读取上传内容，并在后端强制执行大小限制。"""
    content = bytearray()
    while True:
        remaining = MAX_UPLOAD_BYTES - len(content)
        # 多读 1 字节，可靠识别恰好超过上限的文件。
        chunk = await file.read(min(UPLOAD_READ_CHUNK_BYTES, remaining + 1))
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > MAX_UPLOAD_BYTES:
            limit_mb = MAX_UPLOAD_BYTES / (1024 * 1024)
            raise CustomException(f"文件大小不能超过 {limit_mb:g}MB")
    return bytes(content)


async def _upsert_knowledge_metadata(info: dict, original_name: str) -> Knowledge:
    """在一个 MySQL 事务内更新文档指针并清理同名重复记录。"""
    async with in_transaction() as connection:
        rows = await Knowledge.filter(original_name=original_name).using_db(
            connection
        ).order_by("id")
        if rows:
            canonical = rows[0]
            await Knowledge.filter(id=canonical.id).using_db(connection).update(
                filename=info["doc_id"],
                original_name=original_name,
                file_size=info["file_size"],
                chunk_count=info["chunk_count"],
            )
            duplicate_ids = [row.id for row in rows[1:]]
            if duplicate_ids:
                await Knowledge.filter(id__in=duplicate_ids).using_db(connection).delete()
            knowledge_id = canonical.id
        else:
            knowledge = await Knowledge.create(
                using_db=connection,
                filename=info["doc_id"],
                original_name=original_name,
                file_size=info["file_size"],
                chunk_count=info["chunk_count"],
            )
            knowledge_id = knowledge.id

    return await Knowledge.get(id=knowledge_id)


@router.post("/preview")
async def preview(file: UploadFile = File(...)):
    """解析 PDF 并返回清理、标题识别和分块预览，不写入 Chroma/MySQL。"""
    original_name = os.path.basename((file.filename or "").replace("\\", "/"))
    extension = os.path.splitext(original_name)[1].lower()
    if extension != ".pdf":
        raise CustomException("当前仅对 PDF 提供入库前预览")
    file_bytes = await _read_upload_limited(file)
    if not file_bytes:
        raise CustomException("文件内容为空")
    try:
        result = knowledge_service.preview_document(file_bytes, original_name)
    except (ValueError, RuntimeError) as exc:
        raise CustomException(str(exc)) from exc
    except Exception as exc:
        logger.exception("PDF 预览解析失败: filename=%s", original_name)
        raise CustomException("PDF 预览解析失败，请检查文件内容") from exc
    return Result.success(result)


@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    original_name = os.path.basename((file.filename or "").replace("\\", "/"))
    ext = os.path.splitext(original_name)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        supported = " ".join(sorted(ALLOWED_EXTENSIONS))
        raise CustomException(f"不支持的文件格式: {ext or '[无扩展名]'}，支持: {supported}")

    file_bytes = await _read_upload_limited(file)
    if not file_bytes:
        raise CustomException("文件内容为空")

    try:
        info = knowledge_service.add_document(file_bytes, original_name)
    except (ValueError, RuntimeError) as exc:
        raise CustomException(str(exc)) from exc
    except Exception as exc:
        logger.exception("知识库文档构建失败: filename=%s", original_name)
        raise CustomException("知识库向量写入失败，请重启后端后重试") from exc

    try:
        knowledge = await _upsert_knowledge_metadata(info, original_name)
    except Exception as exc:
        # Chroma 和 MySQL 没有跨存储事务：SQL 写入失败时必须补偿删除已经
        # 写入的向量，保证“上传成功”不会只留下孤儿 chunk。
        try:
            restored = bool(info.get("unchanged"))
            if info.get("replaced_existing"):
                restored = knowledge_service.rollback_replacement(info["doc_id"])
            deleted_chunks = 0 if restored else knowledge_service.delete_document(info["doc_id"])
            if not restored and deleted_chunks != info["chunk_count"]:
                logger.error(
                    "上传回滚不完整: doc_id=%s expected=%s deleted=%s",
                    info["doc_id"],
                    info["chunk_count"],
                    deleted_chunks,
                )
        except Exception:
            logger.exception("上传回滚失败: doc_id=%s", info["doc_id"])
        raise CustomException("知识库元数据保存失败，已尝试回滚向量数据") from exc

    knowledge_service.complete_replacement(info["doc_id"])
    if info.get("unchanged"):
        logger.info(
            "知识库重复上传已复用现有索引: filename=%s doc_id=%s",
            original_name,
            info["doc_id"],
        )

    return Result.success({
        "id": knowledge.id,
        "filename": knowledge.filename,
        "original_name": knowledge.original_name,
        "file_size": knowledge.file_size,
        "chunk_count": knowledge.chunk_count,
        "created_at": knowledge.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "unchanged": bool(info.get("unchanged")),
        "replaced_existing": bool(info.get("replaced_existing")),
    })


@router.get("/list")
async def doc_list():
    docs = await Knowledge.all().order_by("-created_at")
    result = []
    for d in docs:
        result.append({
            "id": d.id,
            "filename": d.filename,
            "original_name": d.original_name,
            "file_size": d.file_size,
            "chunk_count": d.chunk_count,
            "created_at": d.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        })
    return Result.success(result)


@router.delete("/delete/{doc_id}")
async def delete(doc_id: int):
    knowledge = await Knowledge.get_or_none(id=doc_id)
    if not knowledge:
        raise CustomException("文档不存在")

    expected_chunks = int(knowledge.chunk_count or 0)
    try:
        snapshot = knowledge_service.snapshot_document(
            knowledge.filename,
            expected_count=expected_chunks,
        )
        knowledge_service.delete_document(
            knowledge.filename,
            expected_count=expected_chunks,
        )
    except ValueError as exc:
        logger.error("删除预检失败: doc_id=%s error=%s", knowledge.filename, exc)
        raise CustomException("向量数据不完整，未执行删除，请先检查知识库健康状态") from exc
    except Exception as exc:
        raise CustomException("向量数据删除失败，未删除知识库记录") from exc

    try:
        await knowledge.delete()
    except Exception as exc:
        try:
            knowledge_service.restore_document_snapshot(snapshot)
        except Exception as restore_exc:
            logger.exception("SQL 删除失败且 Chroma 快照恢复失败: doc_id=%s", knowledge.filename)
            raise CustomException("知识库删除失败且向量恢复失败，请立即检查健康状态") from restore_exc
        raise CustomException("知识库记录删除失败，向量数据已恢复") from exc
    return Result.success()


@router.get("/stats")
async def stats():
    stats = knowledge_service.get_stats()
    doc_count = await Knowledge.all().count()
    return Result.success({"document_count": doc_count, "chunk_count": stats["total_chunks"]})


@router.get("/health")
async def health():
    """管理员只读检查 MySQL 元数据与 Chroma 索引的一致性。"""
    docs = await Knowledge.all().order_by("id")
    sql_documents = [{
        "id": item.id,
        "filename": item.filename,
        "original_name": item.original_name,
        "chunk_count": item.chunk_count,
        "file_size": item.file_size,
    } for item in docs]
    report = knowledge_service.reconcile_metadata(sql_documents)
    if report["healthy"]:
        logger.info("知识库健康检查通过: summary=%s", report["summary"])
    else:
        logger.warning(
            "知识库健康检查发现问题: summary=%s issue_codes=%s",
            report["summary"],
            [item["code"] for item in report["issues"]],
        )
    return Result.success(report)
