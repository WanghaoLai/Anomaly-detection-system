# 文件上传和下载
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Depends, Query, Request
from starlette.responses import FileResponse

from common.auth import get_current_user
from common.exception_handler import CustomException
from common.result import Result

UPLOAD_DIR = Path("files")
UPLOAD_DIR.mkdir(exist_ok=True)

CATEGORY_DIRS = {
    "avatar": "avatars",
    "image": "images",
    "inference": "inference",
}
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

router = APIRouter(prefix="/files")


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
        raise CustomException(f"不支持的文件格式: {ext}")

    subdir = UPLOAD_DIR / CATEGORY_DIRS[category]
    subdir.mkdir(parents=True, exist_ok=True)

    date_prefix = datetime.now().strftime("%Y%m%d")
    unique_name = f"{date_prefix}_{uuid.uuid4().hex[:8]}{ext}"
    file_path = subdir / unique_name

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    base_url = str(request.base_url).rstrip("/")
    relative_path = f"{CATEGORY_DIRS[category]}/{unique_name}"
    return Result.success(f"{base_url}/files/download/{relative_path}")


@router.get("/download/{file_path:path}")
async def download_file(file_path: str):
    """下载文件，兼容旧的扁平结构与新的分类子目录。"""
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
