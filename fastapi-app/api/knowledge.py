"""知识库管理 API（仅管理员）"""
import asyncio
import logging
import os

from common.auth import get_current_admin
from common.exception_handler import CustomException
from common.result import Result
from fastapi import APIRouter, Depends, File, Form, UploadFile
from models import Knowledge, KnowledgeReleaseOperation
from services.knowledge_service import (
    SUPPORTED_DOCUMENT_EXTENSIONS,
    knowledge_service,
)
from services.rag.document import (
    LocalClamAvScanner,
    UploadSecurityPolicy,
    validate_upload_content,
)
from settings import AI_CONFIG
from tortoise.transactions import in_transaction

router = APIRouter(prefix="/knowledge", dependencies=[Depends(get_current_admin)])

logger = logging.getLogger(__name__)
_release_operation_lock = asyncio.Lock()

ALLOWED_EXTENSIONS = SUPPORTED_DOCUMENT_EXTENSIONS
MAX_UPLOAD_BYTES = int(AI_CONFIG.get("rag_max_upload_bytes", 20 * 1024 * 1024))
UPLOAD_READ_CHUNK_BYTES = 1024 * 1024
if MAX_UPLOAD_BYTES <= 0:
    raise RuntimeError("AI_RAG_MAX_UPLOAD_BYTES 必须大于 0")

UPLOAD_SECURITY_ENABLED = bool(AI_CONFIG.get("rag_upload_security_enabled", True))
MALWARE_SCAN_ENABLED = bool(AI_CONFIG.get("rag_malware_scan_enabled", True))
UPLOAD_SECURITY_POLICY = UploadSecurityPolicy(
    max_archive_entries=int(AI_CONFIG.get("rag_archive_max_entries", 5000)),
    max_archive_uncompressed_bytes=int(
        AI_CONFIG.get("rag_archive_max_uncompressed_bytes", 200 * 1024 * 1024)
    ),
    max_archive_compression_ratio=float(
        AI_CONFIG.get("rag_archive_max_compression_ratio", 100.0)
    ),
)
MALWARE_SCANNER = LocalClamAvScanner(
    str(AI_CONFIG.get("rag_clamav_path") or "/usr/local/bin/clamscan"),
    expected_version=str(AI_CONFIG.get("rag_clamav_version") or "1.5.4"),
    database_path=str(AI_CONFIG.get("rag_clamav_database_path") or ""),
    certs_path=str(AI_CONFIG.get("rag_clamav_certs_path") or ""),
    timeout_seconds=float(AI_CONFIG.get("rag_clamav_timeout_seconds", 120.0)),
    max_signature_age_seconds=float(
        AI_CONFIG.get("rag_clamav_max_signature_age_seconds", 86400.0)
    ),
)


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


async def _secure_upload(
    file_bytes: bytes,
    original_name: str,
    declared_media_type: str | None,
) -> None:
    """在任何文档解析、OCR 或持久化之前执行本地安全门禁。"""
    if UPLOAD_SECURITY_ENABLED:
        validate_upload_content(
            file_bytes,
            original_name,
            declared_media_type,
            policy=UPLOAD_SECURITY_POLICY,
        )
    if MALWARE_SCAN_ENABLED:
        await asyncio.to_thread(MALWARE_SCANNER.scan, file_bytes, original_name)


async def _upsert_knowledge_metadata_using(
    info: dict, original_name: str, connection
) -> int:
    """在调用方事务内更新文档指针并清理同名重复记录。"""
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
        return canonical.id
    knowledge = await Knowledge.create(
        using_db=connection,
        filename=info["doc_id"],
        original_name=original_name,
        file_size=info["file_size"],
        chunk_count=info["chunk_count"],
    )
    return knowledge.id


async def _upsert_knowledge_metadata(info: dict, original_name: str) -> Knowledge:
    """兼容独立调用；发布链路使用上方的显式事务版。"""
    async with in_transaction() as connection:
        knowledge_id = await _upsert_knowledge_metadata_using(
            info, original_name, connection
        )
    return await Knowledge.get(id=knowledge_id)


def _knowledge_snapshot(rows) -> list[dict]:
    return [{
        "id": item.id,
        "filename": item.filename,
        "original_name": item.original_name,
        "file_size": item.file_size,
        "chunk_count": item.chunk_count,
    } for item in rows]


async def _restore_knowledge_snapshot(payload: dict, connection) -> None:
    original_name = payload["original_name"]
    await Knowledge.filter(original_name=original_name).using_db(connection).delete()
    for item in payload.get("previous_rows") or []:
        await Knowledge.create(using_db=connection, **item)


async def _publish_recorded_release(operation: KnowledgeReleaseOperation) -> None:
    """发布 PENDING release；进程在指针切换后退出时可幂等收敛。"""
    if knowledge_service.current_release_id() != operation.release_id:
        knowledge_service.publish_staged_release(operation.release_id)
    await KnowledgeReleaseOperation.filter(
        release_id=operation.release_id,
        status="PENDING",
    ).update(status="PUBLISHED", error_message=None)


async def recover_pending_knowledge_releases() -> None:
    """应用启动时先完成已提交的元数据发布，再开放检索和管理接口。"""
    pending = await KnowledgeReleaseOperation.filter(status="PENDING").order_by(
        "created_at"
    )
    for operation in pending:
        try:
            await _publish_recorded_release(operation)
        except Exception as exc:
            await KnowledgeReleaseOperation.filter(
                release_id=operation.release_id
            ).update(error_message=str(exc)[:1000])
            raise RuntimeError(
                f"知识库发布 {operation.release_id} 尚未恢复，拒绝启动"
            ) from exc


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
        await _secure_upload(file_bytes, original_name, file.content_type)
        result = await knowledge_service.preview_document_async(
            file_bytes, original_name
        )
    except (ValueError, RuntimeError) as exc:
        raise CustomException(str(exc)) from exc
    except Exception as exc:
        logger.exception("PDF 预览解析失败: filename=%s", original_name)
        raise CustomException("PDF 预览解析失败，请检查文件内容") from exc
    return Result.success(result)


@router.post("/upload")
async def upload(
    file: UploadFile = File(...),
    visibility: str = Form("internal"),
    allowed_roles: str = Form("管理员,用户"),
    allowed_user_ids: str = Form(""),
):
    original_name = os.path.basename((file.filename or "").replace("\\", "/"))
    ext = os.path.splitext(original_name)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        supported = " ".join(sorted(ALLOWED_EXTENSIONS))
        raise CustomException(f"不支持的文件格式: {ext or '[无扩展名]'}，支持: {supported}")

    file_bytes = await _read_upload_limited(file)
    if not file_bytes:
        raise CustomException("文件内容为空")

    try:
        await _secure_upload(file_bytes, original_name, file.content_type)
        # P1 阶段只构建影子 collection，此时在线检索指针未变。
        info = await knowledge_service.stage_document_release_async(
            file_bytes,
            original_name,
            visibility=visibility,
            allowed_roles=allowed_roles,
            allowed_user_ids=allowed_user_ids,
        )
    except (ValueError, RuntimeError) as exc:
        raise CustomException(str(exc)) from exc
    except Exception as exc:
        logger.exception("知识库文档构建失败: filename=%s", original_name)
        raise CustomException("知识库影子索引构建失败，当前发布版本未受影响") from exc

    async with _release_operation_lock:
        operation = None
        try:
            async with in_transaction() as connection:
                previous = await Knowledge.filter(
                    original_name=original_name
                ).using_db(connection).order_by("id")
                knowledge_id = await _upsert_knowledge_metadata_using(
                    info, original_name, connection
                )
                if not info.get("unchanged"):
                    operation = await KnowledgeReleaseOperation.create(
                        using_db=connection,
                        release_id=info["release_id"],
                        operation="UPLOAD",
                        status="PENDING",
                        payload_json={
                            "original_name": original_name,
                            "previous_rows": _knowledge_snapshot(previous),
                        },
                    )
            if operation is not None:
                await _publish_recorded_release(operation)
            knowledge = await Knowledge.get(id=knowledge_id)
        except Exception as exc:
            # 指针已经切换时保留 PENDING，启动恢复会把它幂等标为 PUBLISHED。
            if operation is not None and knowledge_service.current_release_id() != operation.release_id:
                async with in_transaction() as connection:
                    await _restore_knowledge_snapshot(operation.payload_json, connection)
                    await KnowledgeReleaseOperation.filter(
                        release_id=operation.release_id
                    ).using_db(connection).update(
                        status="ROLLED_BACK", error_message=str(exc)[:1000]
                    )
                try:
                    knowledge_service.discard_staged_release(operation.release_id)
                except Exception:
                    logger.exception("清理未发布影子索引失败: %s", operation.release_id)
            raise CustomException("知识库发布失败，当前版本已保持不变") from exc

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

    try:
        staged = await knowledge_service.stage_delete_release_async(knowledge.filename)
    except ValueError as exc:
        logger.error("删除影子索引预检失败: doc_id=%s error=%s", knowledge.filename, exc)
        raise CustomException(str(exc)) from exc
    except Exception as exc:
        raise CustomException("删除影子索引构建失败，当前版本未受影响") from exc

    async with _release_operation_lock:
        operation = None
        try:
            async with in_transaction() as connection:
                rows = await Knowledge.filter(
                    original_name=knowledge.original_name
                ).using_db(connection).order_by("id")
                payload = {
                    "original_name": knowledge.original_name,
                    "previous_rows": _knowledge_snapshot(rows),
                }
                deleted = await Knowledge.filter(id=doc_id).using_db(connection).delete()
                if deleted != 1:
                    raise RuntimeError("知识库 MySQL 记录删除数量异常")
                operation = await KnowledgeReleaseOperation.create(
                    using_db=connection,
                    release_id=staged["release_id"],
                    operation="DELETE",
                    status="PENDING",
                    payload_json=payload,
                )
            await _publish_recorded_release(operation)
        except Exception as exc:
            if operation is not None and knowledge_service.current_release_id() != operation.release_id:
                async with in_transaction() as connection:
                    await _restore_knowledge_snapshot(operation.payload_json, connection)
                    await KnowledgeReleaseOperation.filter(
                        release_id=operation.release_id
                    ).using_db(connection).update(
                        status="ROLLED_BACK", error_message=str(exc)[:1000]
                    )
                try:
                    knowledge_service.discard_staged_release(operation.release_id)
                except Exception:
                    logger.exception("清理删除影子索引失败: %s", operation.release_id)
            raise CustomException("知识库删除失败，当前发布版本已保持不变") from exc
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
