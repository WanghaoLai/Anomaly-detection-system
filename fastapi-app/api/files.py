# 文件上传和下载
import io
import logging
import mimetypes
import os
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Query
from PIL import Image, UnidentifiedImageError
from starlette.responses import FileResponse

from common.auth import get_current_user
from common.exception_handler import CustomException
from common.result import Result
from models import Admin, StoredFile, User
from settings import API_PREFIX, FILE_UPLOAD_DIR

logger = logging.getLogger(__name__)

UPLOAD_DIR = FILE_UPLOAD_DIR
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

CATEGORY_DIRS = {
    "avatar": "avatars",
    "image": "images",
    "inference": "inference",
}
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# 各扩展名允许的 Magic Bytes 签名变体：外层为可任选其一的版本（如 GIF87a/
# GIF89a），每个变体内为必须全部匹配的（偏移量, 字节序列）列表。用于校验文件
# 头与声明的图片类型一致，拦截改扩展名伪装的任意文件。
MAGIC_SIGNATURES = {
    ".jpg": (((0, b"\xff\xd8\xff"),),),
    ".jpeg": (((0, b"\xff\xd8\xff"),),),
    ".png": (((0, b"\x89PNG\r\n\x1a\n"),),),
    ".gif": (((0, b"GIF87a"),), ((0, b"GIF89a"),)),
    ".bmp": (((0, b"BM"),),),
    ".webp": (((0, b"RIFF"), (8, b"WEBP")),),
}

router = APIRouter(prefix="/files")


def _matches_signatures(ext: str, content: bytes) -> bool:
    return any(
        all(content[offset:offset + len(expected)] == expected for offset, expected in variant)
        for variant in MAGIC_SIGNATURES[ext]
    )


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    category: str = Query("avatar", description="文件分类: avatar / image / inference"),
    current_user: dict = Depends(get_current_user),
):
    """上传单个文件，按分类保存到子目录，生成唯一文件名避免冲突。"""
    if category not in CATEGORY_DIRS:
        raise HTTPException(status_code=422, detail="不支持的文件分类")

    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        logger.warning("拒绝不支持的扩展名: filename=%s ext=%s", file.filename, ext)
        raise HTTPException(status_code=415, detail=f"不支持的文件格式: {ext}")

    # 只读取上限+1 字节即可判定是否超限，避免超大请求占满内存
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        logger.warning("上传文件超过大小上限: filename=%s", file.filename)
        raise HTTPException(
            status_code=413,
            detail=f"文件超过 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB 大小上限",
        )

    if not _matches_signatures(ext, content):
        logger.warning("文件头与扩展名不符: filename=%s ext=%s", file.filename, ext)
        raise HTTPException(status_code=415, detail="文件内容与声明的图片格式不一致")

    try:
        with Image.open(io.BytesIO(content)) as image:
            image.verify()
    except UnidentifiedImageError:
        logger.warning("Pillow 无法识别上传内容: filename=%s ext=%s", file.filename, ext)
        raise HTTPException(status_code=415, detail="无法识别的图片文件")
    except Exception as exc:
        logger.warning("图片解码校验失败: filename=%s ext=%s error=%s", file.filename, ext, exc)
        raise HTTPException(status_code=400, detail="图片文件损坏或内容不完整")

    subdir = UPLOAD_DIR / CATEGORY_DIRS[category]
    subdir.mkdir(parents=True, exist_ok=True)

    date_prefix = datetime.now().strftime("%Y%m%d")
    unique_name = f"{date_prefix}_{uuid.uuid4().hex[:8]}{ext}"
    file_path = subdir / unique_name

    relative_path = f"{CATEGORY_DIRS[category]}/{unique_name}"
    file_id = str(uuid.uuid4())
    temp_path = file_path.with_suffix(f"{file_path.suffix}.uploading")
    access_scope = "AUTHENTICATED" if category == "avatar" else "OWNER"
    original_name = Path(file.filename or unique_name).name[:255]

    try:
        temp_path.write_bytes(content)
        os.replace(temp_path, file_path)
        await StoredFile.create(
            id=file_id,
            category=category,
            relative_path=relative_path,
            original_name=original_name,
            owner_id=int(current_user["user_id"]),
            owner_role=str(current_user["role"]),
            access_scope=access_scope,
            size_bytes=len(content),
            media_type=file.content_type or mimetypes.guess_type(original_name)[0],
        )
    except Exception:
        temp_path.unlink(missing_ok=True)
        file_path.unlink(missing_ok=True)
        logger.exception("文件与元数据持久化失败: file_id=%s", file_id)
        raise CustomException("文件保存失败")

    return Result.success(f"{API_PREFIX}/files/download/{file_id}")


def _is_authorized(record: StoredFile, current_user: dict) -> bool:
    if record.access_scope == "AUTHENTICATED":
        return True
    if current_user.get("role") == "管理员":
        return True
    return (
        record.owner_id == int(current_user["user_id"])
        and record.owner_role == str(current_user["role"])
    )


async def _is_referenced_legacy_avatar(relative_path: str) -> bool:
    """旧版头像没有元数据；只对仍被账号记录精确引用的头像兼容放行。"""
    if not relative_path.startswith("avatars/"):
        return False
    suffix = f"/files/download/{relative_path}"
    return (
        await User.filter(avatar__endswith=suffix).exists()
        or await Admin.filter(avatar__endswith=suffix).exists()
    )


@router.get("/download/{file_ref:path}")
async def download_file(
    file_ref: str,
    current_user: dict = Depends(get_current_user),
):
    """根据文件对象授权下载；拒绝时统一返回 404，避免泄露对象存在性。"""
    if ".." in file_ref or file_ref.startswith("/"):
        raise HTTPException(status_code=404, detail="文件不存在")

    record = await StoredFile.get_or_none(id=file_ref)
    if record is not None:
        if not _is_authorized(record, current_user):
            raise HTTPException(status_code=404, detail="文件不存在")
        relative_path = record.relative_path
        download_name = record.original_name
        media_type = record.media_type or "application/octet-stream"
    else:
        # 兼容升级前头像 URL；非头像历史路径默认关闭（fail closed）。
        if not await _is_referenced_legacy_avatar(file_ref):
            raise HTTPException(status_code=404, detail="文件不存在")
        relative_path = file_ref
        download_name = Path(file_ref).name
        media_type = mimetypes.guess_type(download_name)[0] or "application/octet-stream"

    root = UPLOAD_DIR.resolve()
    file_location = (UPLOAD_DIR / relative_path).resolve()
    if root not in file_location.parents or not file_location.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")

    return FileResponse(
        path=str(file_location),
        filename=download_name,
        media_type=media_type,
    )
