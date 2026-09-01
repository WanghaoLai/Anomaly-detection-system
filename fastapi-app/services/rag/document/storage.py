"""P1 知识库事实源：原始文件、统一文档 DocStore 与发布清单。

所有写入都在同一目录内通过 ``os.replace`` 原子提交。Chroma 不是
事实源；它可以从 DocStore 中的 Node 完整重建。
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..core.contracts import Document, Node, SourceInfo


DOCUMENT_SCHEMA_VERSION = "unified-document-v1"
NODE_SCHEMA_VERSION = "deterministic-node-v1"
RELEASE_SCHEMA_VERSION = "shadow-release-v1"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            # 某些文件系统不支持目录 fsync；os.replace 仍保持进程级原子性。
            pass
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _safe_storage_path(root: Path, storage_key: str) -> Path:
    candidate = (root / storage_key).resolve()
    resolved_root = root.resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise ValueError("存储定位符越界")
    return candidate


class ContentAddressedFileStore:
    """以 SHA256 为地址保存原始字节，同内容只存一份。"""

    def __init__(self, root: Path):
        self.root = Path(root)

    @staticmethod
    def storage_key(content_hash: str) -> str:
        if len(content_hash) != 64 or any(ch not in "0123456789abcdef" for ch in content_hash):
            raise ValueError("原始文件 SHA256 无效")
        return f"raw/{content_hash[:2]}/{content_hash}.bin"

    def put(self, content: bytes, filename: str, extension: str) -> SourceInfo:
        raw = bytes(content)
        content_hash = sha256_bytes(raw)
        key = self.storage_key(content_hash)
        target = _safe_storage_path(self.root, key)
        if target.exists():
            if target.stat().st_size != len(raw) or sha256_bytes(target.read_bytes()) != content_hash:
                raise RuntimeError("内容寻址原文件校验失败，已拒绝覆盖")
        else:
            _atomic_write(target, raw)
        media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return SourceInfo(
            filename=filename,
            extension=extension,
            media_type=media_type,
            byte_size=len(raw),
            sha256=content_hash,
            storage_key=key,
            uploaded_at=utc_now_iso(),
        )

    def verify(self, source: dict[str, Any]) -> list[str]:
        key = source.get("storage_key")
        if not key:
            return ["原始文件不可用"]
        try:
            path = _safe_storage_path(self.root, str(key))
        except ValueError as exc:
            return [str(exc)]
        if not path.is_file():
            return [f"原始文件缺失: {key}"]
        payload = path.read_bytes()
        errors = []
        if len(payload) != int(source.get("byte_size") or -1):
            errors.append("原始文件大小不一致")
        if sha256_bytes(payload) != source.get("sha256"):
            errors.append("原始文件 SHA256 不一致")
        return errors


class JsonDocumentStore:
    """保存框架无关的 Document/Node JSON，是重建索引的唯一输入。"""

    def __init__(self, root: Path):
        self.root = Path(root)

    def path_for(self, document_id: str) -> Path:
        if not document_id or any(ch not in "0123456789abcdef-" for ch in document_id):
            raise ValueError("document_id 无效")
        return _safe_storage_path(self.root, f"docstore/{document_id}.json")

    def put(self, document: Document, nodes: Iterable[Node], diagnostics: dict) -> dict:
        if document.document_id is None or document.source is None:
            raise ValueError("统一文档必须包含 document_id 和 source")
        node_records = []
        seen_ids: set[str] = set()
        for node in nodes:
            if not node.node_id:
                raise ValueError("Node 缺少稳定 node_id")
            if node.node_id in seen_ids:
                raise ValueError(f"文档内存在重复 Node: {node.node_id}")
            seen_ids.add(node.node_id)
            node_records.append({
                "node_id": node.node_id,
                "text": node.text,
                "text_sha256": sha256_bytes(node.text.encode("utf-8")),
                "metadata": dict(node.metadata),
            })
        record = {
            "schema_version": DOCUMENT_SCHEMA_VERSION,
            "document_id": document.document_id,
            "text": document.text,
            "text_sha256": sha256_bytes(document.text.encode("utf-8")),
            "metadata": dict(document.metadata),
            "source": asdict(document.source),
            "diagnostics": dict(diagnostics),
            "nodes": node_records,
        }
        path = self.path_for(document.document_id)
        payload = _canonical_json(record)
        if path.exists():
            existing = path.read_bytes()
            if existing != payload:
                raise RuntimeError(
                    f"DocStore 中的确定性文档发生冲突: {document.document_id}"
                )
        else:
            _atomic_write(path, payload)
        return record

    def put_record(self, record: dict) -> None:
        """只用于从 P0 Chroma 启动历史文档；记录仍需通过完整校验。"""
        document_id = str(record.get("document_id") or "")
        self.validate_record(record)
        path = self.path_for(document_id)
        payload = _canonical_json(record)
        if path.exists() and path.read_bytes() != payload:
            raise RuntimeError(f"历史 DocStore 文档冲突: {document_id}")
        if not path.exists():
            _atomic_write(path, payload)

    def get(self, document_id: str) -> dict:
        path = self.path_for(document_id)
        if not path.is_file():
            raise FileNotFoundError(f"DocStore 文档不存在: {document_id}")
        record = json.loads(path.read_text(encoding="utf-8"))
        self.validate_record(record)
        return record

    @staticmethod
    def validate_record(record: dict) -> None:
        if record.get("schema_version") != DOCUMENT_SCHEMA_VERSION:
            raise ValueError("DocStore 文档 schema_version 不一致")
        text = record.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("DocStore 文档正文无效")
        if sha256_bytes(text.encode("utf-8")) != record.get("text_sha256"):
            raise ValueError("DocStore 文档正文 SHA256 不一致")
        node_ids: set[str] = set()
        for node in record.get("nodes") or []:
            node_id = node.get("node_id")
            if not node_id or node_id in node_ids:
                raise ValueError(f"DocStore Node ID 缺失或重复: {node_id}")
            node_ids.add(node_id)
            if sha256_bytes(str(node.get("text") or "").encode("utf-8")) != node.get("text_sha256"):
                raise ValueError(f"DocStore Node 正文 SHA256 不一致: {node_id}")


class ReleaseManifestStore:
    """保存不可变候选集合与单一原子发布指针。"""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.active_path = self.root / "active_release.json"

    def release_path(self, release_id: str) -> Path:
        if not release_id or any(ch not in "0123456789abcdef" for ch in release_id):
            raise ValueError("release_id 无效")
        return _safe_storage_path(self.root, f"releases/{release_id}.json")

    def put(self, manifest: dict) -> None:
        if manifest.get("schema_version") != RELEASE_SCHEMA_VERSION:
            raise ValueError("发布清单 schema_version 无效")
        path = self.release_path(str(manifest.get("release_id") or ""))
        payload = _canonical_json(manifest)
        if path.exists() and path.read_bytes() != payload:
            raise RuntimeError(f"发布清单冲突: {manifest.get('release_id')}")
        if not path.exists():
            _atomic_write(path, payload)

    def get(self, release_id: str) -> dict:
        path = self.release_path(release_id)
        if not path.is_file():
            raise FileNotFoundError(f"发布清单不存在: {release_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def active(self) -> dict | None:
        if not self.active_path.is_file():
            return None
        pointer = json.loads(self.active_path.read_text(encoding="utf-8"))
        if pointer.get("legacy") is True:
            return None
        if pointer.get("schema_version") != RELEASE_SCHEMA_VERSION:
            raise RuntimeError("发布指针 schema_version 不一致")
        release_id = str(pointer.get("release_id") or "")
        manifest_path = self.release_path(release_id)
        if not manifest_path.is_file():
            raise RuntimeError(f"发布指针引用的清单不存在: {release_id}")
        actual_hash = sha256_bytes(manifest_path.read_bytes())
        if actual_hash != pointer.get("manifest_sha256"):
            raise RuntimeError("发布清单 SHA256 与指针不一致")
        return pointer

    def publish(self, manifest: dict) -> dict | None:
        previous = self.active()
        pointer = {
            "schema_version": RELEASE_SCHEMA_VERSION,
            "release_id": manifest["release_id"],
            "collection_name": manifest["collection_name"],
            "manifest_sha256": sha256_bytes(
                self.release_path(manifest["release_id"]).read_bytes()
            ),
            "published_at": utc_now_iso(),
        }
        _atomic_write(self.active_path, _canonical_json(pointer))
        return previous

    def restore(self, previous: dict | None) -> None:
        if previous is None:
            # 显式 tombstone 通过 os.replace 回退，避免 unlink 的非原子窗口。
            _atomic_write(self.active_path, _canonical_json({
                "schema_version": RELEASE_SCHEMA_VERSION,
                "legacy": True,
                "collection_name": "knowledge_base",
                "restored_at": utc_now_iso(),
            }))
            return
        _atomic_write(self.active_path, _canonical_json(previous))


RELEASE_SMOKE_SCHEMA_VERSION = "rag-release-smoke-attestation-v1"


class ReleaseSmokeStore:
    """Immutable evidence that a candidate release passed the fixed smoke set."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def path_for(self, release_id: str) -> Path:
        if not release_id or any(ch not in "0123456789abcdef" for ch in release_id):
            raise ValueError("release_id 无效")
        return _safe_storage_path(
            self.root, f"release_smoke/{release_id}.json"
        )

    def put(self, attestation: dict) -> None:
        if attestation.get("schema_version") != RELEASE_SMOKE_SCHEMA_VERSION:
            raise ValueError("Release Smoke 证明 schema_version 无效")
        release_id = str(attestation.get("release_id") or "")
        path = self.path_for(release_id)
        payload = _canonical_json(attestation)
        if path.exists() and path.read_bytes() != payload:
            raise RuntimeError(f"Release Smoke 证明冲突: {release_id}")
        if not path.exists():
            _atomic_write(path, payload)

    def get(self, release_id: str) -> dict:
        path = self.path_for(release_id)
        if not path.is_file():
            raise FileNotFoundError(f"Release Smoke 证明不存在: {release_id}")
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("schema_version") != RELEASE_SMOKE_SCHEMA_VERSION:
            raise RuntimeError("Release Smoke 证明 schema_version 不一致")
        return value


class KnowledgeArtifactRepository:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.files = ContentAddressedFileStore(self.root)
        self.documents = JsonDocumentStore(self.root)
        self.releases = ReleaseManifestStore(self.root)
        self.release_smokes = ReleaseSmokeStore(self.root)


def deterministic_document_id(
    filename: str,
    content_hash: str,
    ingestion_schema_version: str = "",
) -> str:
    identity = {
        "schema": DOCUMENT_SCHEMA_VERSION,
        "filename": filename,
        "content_sha256": content_hash,
        "ingestion_schema_version": ingestion_schema_version,
    }
    return hashlib.sha256(_canonical_json(identity)).hexdigest()[:32]


def deterministic_node_id(document_id: str, index: int, text: str, metadata: dict) -> str:
    identity = {
        "schema": NODE_SCHEMA_VERSION,
        "document_id": document_id,
        "index": int(index),
        "text_sha256": sha256_bytes(text.encode("utf-8")),
        "char_start": metadata.get("start"),
        "char_end": metadata.get("end"),
        "heading_path": metadata.get("heading_path"),
    }
    return hashlib.sha256(_canonical_json(identity)).hexdigest()


__all__ = [
    "ContentAddressedFileStore",
    "DOCUMENT_SCHEMA_VERSION",
    "JsonDocumentStore",
    "KnowledgeArtifactRepository",
    "NODE_SCHEMA_VERSION",
    "RELEASE_SCHEMA_VERSION",
    "RELEASE_SMOKE_SCHEMA_VERSION",
    "ReleaseManifestStore",
    "ReleaseSmokeStore",
    "deterministic_document_id",
    "deterministic_node_id",
    "sha256_bytes",
    "utc_now_iso",
]
