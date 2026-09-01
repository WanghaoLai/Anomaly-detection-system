# 文件上传和下载
import io
import logging
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Query, Request
from PIL import Image, UnidentifiedImageError
from starlette.responses import FileResponse

from common.auth import get_current_user
from common.exception_handler import CustomException
from common.result import Result

logger = logging.getLogger(__name__)

UPLOAD_DIR = Path("files")
UPLOAD_DIR.mkdir(exist_ok=True)

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


@router.post("/upload", dependencies=[Depends(get_current_user)])
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    category: str = Query("avatar", description="文件分类: avatar / image / inference"),
):
    """上传单个文件，按分类保存到子目录，生成唯一文件名避免冲突。"""
    if category not in CATEGORY_DIRS:
        category = "image"

    ext = Path(file.filename).suffix.lower()
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

    file_path.write_bytes(content)

    base_url = str(request.base_url).rstrip("/")
    relative_path = f"{CATEGORY_DIRS[category]}/{unique_name}"
    return Result.success(f"{base_url}/files/download/{relative_path}")


@router.get(
    "/download/{file_path:path}",
    dependencies=[Depends(get_current_user)],
)
async def download_file(file_path: str):
    """下载文件，兼容旧的扁平结构与新的分类子目录。"""
    # 推理结果等上传产物按用户隔离，未登录请求必须在这里被拒绝；
    # GET 请求同源 Cookie 自动携带，前端 <img> 引用不受影响。
    if ".." in file_path or file_path.startswith("/"):
        raise CustomException("非法的文件路径")

    file_location = UPLOAD_DIR / file_path
    if not file_location.exists() or not file_location.is_file():
        raise CustomException("文件不存在")

    return FileResponse(
        path=str(file_location),
        filename=file_location.name,
        media_type='application/octet-stream',
    )
