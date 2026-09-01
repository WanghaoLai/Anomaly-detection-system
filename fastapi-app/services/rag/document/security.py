"""知识文档在进入解析器之前的本地安全门禁。"""

from __future__ import annotations

import io
import json
import os
import posixpath
import re
import subprocess
import stat
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree


GENERIC_MEDIA_TYPES = frozenset({
    "",
    "application/octet-stream",
    "binary/octet-stream",
})

TEXT_EXTENSIONS = frozenset({
    ".txt", ".md", ".markdown", ".csv", ".html", ".htm",
    ".json", ".xml", ".ipynb",
})
ZIP_EXTENSIONS = frozenset({".docx", ".pptx", ".xlsx", ".epub"})

EXPECTED_MEDIA_TYPES = {
    ".txt": {"text/plain"},
    ".md": {"text/markdown", "text/plain", "text/x-markdown"},
    ".markdown": {"text/markdown", "text/plain", "text/x-markdown"},
    ".csv": {"text/csv", "application/csv", "text/plain"},
    ".html": {"text/html"},
    ".htm": {"text/html"},
    ".json": {"application/json", "text/json", "text/plain"},
    ".ipynb": {"application/x-ipynb+json", "application/json", "text/plain"},
    ".xml": {"application/xml", "text/xml", "text/plain"},
    ".pdf": {"application/pdf"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    },
    ".pptx": {
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    },
    ".xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    },
    ".xls": {"application/vnd.ms-excel", "application/x-ole-storage"},
    ".epub": {"application/epub+zip"},
}

_OLE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
_ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_SIGNATURE_FILENAMES = (
    "daily.cvd", "daily.cld"
)


@dataclass(frozen=True)
class UploadSecurityPolicy:
    max_archive_entries: int = 5000
    max_archive_uncompressed_bytes: int = 200 * 1024 * 1024
    max_archive_compression_ratio: float = 100.0

    def __post_init__(self) -> None:
        if self.max_archive_entries <= 0:
            raise ValueError("压缩容器条目上限必须大于 0")
        if self.max_archive_uncompressed_bytes <= 0:
            raise ValueError("压缩容器解压大小上限必须大于 0")
        if self.max_archive_compression_ratio <= 0:
            raise ValueError("压缩比上限必须大于 0")


def _decode_text(payload: bytes, extension: str) -> str:
    if b"\x00" in payload and not payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise ValueError("文本文件包含二进制空字节")
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"{extension} 文件不是受支持的 Unicode 文本")


def _validate_text_structure(payload: bytes, extension: str) -> None:
    text = _decode_text(payload, extension)
    if extension in {".json", ".ipynb"}:
        try:
            value = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{extension} 文件结构无效") from exc
        if extension == ".ipynb" and not isinstance(value, dict):
            raise ValueError(".ipynb 顶层结构必须是对象")
    elif extension == ".xml":
        if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.IGNORECASE):
            raise ValueError("XML 不允许 DOCTYPE 或 ENTITY 声明")
        try:
            ElementTree.fromstring(text)
        except ElementTree.ParseError as exc:
            raise ValueError(".xml 文件结构无效") from exc


def _safe_archive_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise ValueError("压缩容器包含绝对路径")
    clean = posixpath.normpath(normalized)
    if clean == ".." or clean.startswith("../"):
        raise ValueError("压缩容器包含路径穿越")
    return clean.rstrip("/")


def _validate_zip_structure(
    payload: bytes,
    extension: str,
    policy: UploadSecurityPolicy,
) -> None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"{extension} 文件不是有效的 ZIP 容器") from exc
    with archive:
        entries = archive.infolist()
        if len(entries) > policy.max_archive_entries:
            raise ValueError("压缩容器条目数超过安全上限")
        names: set[str] = set()
        total_size = 0
        total_compressed = 0
        for entry in entries:
            name = _safe_archive_name(entry.filename)
            if name in names and name:
                raise ValueError("压缩容器包含重复路径")
            names.add(name)
            if entry.flag_bits & 0x1:
                raise ValueError("不接受含加密条目的压缩容器")
            unix_mode = (entry.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(unix_mode):
                raise ValueError("压缩容器不允许符号链接条目")
            total_size += int(entry.file_size)
            total_compressed += int(entry.compress_size)
            if total_size > policy.max_archive_uncompressed_bytes:
                raise ValueError("压缩容器总解压大小超过安全上限")
            if entry.file_size > 0:
                ratio = entry.file_size / max(1, entry.compress_size)
                if ratio > policy.max_archive_compression_ratio:
                    raise ValueError("压缩容器单条目压缩比超过安全上限")
        if total_size / max(1, total_compressed) > policy.max_archive_compression_ratio:
            raise ValueError("压缩容器总体压缩比超过安全上限")

        if extension in {".docx", ".pptx", ".xlsx"}:
            if "[Content_Types].xml" not in names:
                raise ValueError("OOXML 容器缺少 [Content_Types].xml")
            required_prefix = {".docx": "word/", ".pptx": "ppt/", ".xlsx": "xl/"}[extension]
            if not any(name.startswith(required_prefix) for name in names):
                raise ValueError(f"{extension} 容器内容家族不匹配")
        elif extension == ".epub":
            try:
                mimetype = archive.read("mimetype", pwd=None)
            except (KeyError, RuntimeError, zipfile.BadZipFile) as exc:
                raise ValueError("EPUB 容器缺少 mimetype") from exc
            if mimetype.strip() != b"application/epub+zip":
                raise ValueError("EPUB mimetype 无效")


def validate_upload_content(
    payload: bytes,
    filename: str,
    declared_media_type: str | None,
    *,
    policy: UploadSecurityPolicy,
) -> dict:
    """验证扩展名、客户端 MIME、Magic Bytes 和容器结构的一致性。"""
    raw = bytes(payload)
    extension = os.path.splitext(filename)[1].lower()
    media_type = str(declared_media_type or "").split(";", 1)[0].strip().lower()
    expected = EXPECTED_MEDIA_TYPES.get(extension, set())
    if media_type not in GENERIC_MEDIA_TYPES and media_type not in expected:
        raise ValueError("文件 MIME 与扩展名不一致")

    if extension == ".pdf":
        if not raw.startswith(b"%PDF-"):
            raise ValueError("PDF Magic Bytes 与扩展名不一致")
    elif extension == ".xls":
        if not raw.startswith(_OLE_MAGIC):
            raise ValueError("XLS Magic Bytes 与扩展名不一致")
    elif extension in ZIP_EXTENSIONS:
        if not raw.startswith(_ZIP_MAGICS):
            raise ValueError("ZIP Magic Bytes 与扩展名不一致")
        _validate_zip_structure(raw, extension, policy)
    elif extension in TEXT_EXTENSIONS:
        _validate_text_structure(raw, extension)
    else:
        raise ValueError("文件扩展名不在安全内容家族中")
    return {
        "extension": extension,
        "declared_media_type": media_type or "unspecified",
        "content_family": (
            "zip" if extension in ZIP_EXTENSIONS else
            "pdf" if extension == ".pdf" else
            "ole" if extension == ".xls" else "text"
        ),
    }


class LocalClamAvScanner:
    """使用固定版本 ClamAV 在本机临时文件上执行 Fail Closed 扫描。"""

    def __init__(
        self,
        executable: str,
        *,
        expected_version: str = "1.5.4",
        database_path: str = "",
        certs_path: str = "",
        timeout_seconds: float = 120.0,
        max_signature_age_seconds: float = 86400.0,
    ) -> None:
        self.executable = str(executable)
        self.expected_version = str(expected_version)
        self.database_path = str(database_path or "")
        self.certs_path = str(certs_path or "")
        self.timeout_seconds = float(timeout_seconds)
        self.max_signature_age_seconds = float(max_signature_age_seconds)

    def _signature_files(self) -> Iterable[Path]:
        roots = [Path(self.database_path)] if self.database_path else [
            Path("/usr/local/share/clamav"),
            Path("/opt/homebrew/var/lib/clamav"),
            Path("/var/lib/clamav"),
        ]
        for root in roots:
            for name in _SIGNATURE_FILENAMES:
                candidate = root / name
                if candidate.is_file():
                    yield candidate

    def _preflight(self) -> None:
        executable = Path(self.executable)
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise RuntimeError("本地恶意文件扫描器不可用")
        environment = os.environ.copy()
        if self.certs_path:
            environment["CVD_CERTS_DIR"] = self.certs_path
        try:
            version = subprocess.run(
                [self.executable, "--version"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
                env=environment,
            ).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("本地恶意文件扫描器不可用") from exc
        if not re.search(rf"\bClamAV\s+{re.escape(self.expected_version)}\b", version):
            raise RuntimeError("本地恶意文件扫描器版本不符合固定契约")
        signatures = list(self._signature_files())
        if not signatures:
            raise RuntimeError("ClamAV 病毒库不可用")
        newest = max(path.stat().st_mtime for path in signatures)
        if time.time() - newest > self.max_signature_age_seconds:
            raise RuntimeError("ClamAV 病毒库超过允许更新时间")

    def scan(self, payload: bytes, filename: str) -> dict:
        self._preflight()
        suffix = os.path.splitext(filename)[1].lower()
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix="rag-upload-", suffix=suffix, delete=False
            ) as stream:
                stream.write(bytes(payload))
                stream.flush()
                os.fsync(stream.fileno())
                temporary_path = Path(stream.name)
            command = [self.executable, "--no-summary", "--infected"]
            if self.database_path:
                command.extend(["--database", self.database_path])
            command.append(str(temporary_path))
            environment = os.environ.copy()
            if self.certs_path:
                environment["CVD_CERTS_DIR"] = self.certs_path
            try:
                completed = subprocess.run(
                    command,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=self.timeout_seconds,
                    env=environment,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise RuntimeError("本地恶意文件扫描失败") from exc
            if completed.returncode == 1:
                raise ValueError("文件未通过恶意内容扫描")
            if completed.returncode != 0:
                raise RuntimeError("本地恶意文件扫描失败")
            return {"scanner": "clamav", "version": self.expected_version, "clean": True}
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass


__all__ = [
    "LocalClamAvScanner",
    "UploadSecurityPolicy",
    "validate_upload_content",
]
