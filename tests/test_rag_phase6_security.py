import io
import asyncio
import os
import stat
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from services.rag.document import (  # noqa: E402
    LocalClamAvScanner,
    ProcessIsolatedDocumentParser,
    UploadSecurityPolicy,
    validate_upload_content,
)
from services.knowledge_service import KnowledgeService  # noqa: E402


def _archive(entries: dict[str, bytes], *, compression=zipfile.ZIP_STORED) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=compression) as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return stream.getvalue()


def _slow_parser_worker(result_queue, payload):
    del result_queue, payload
    time.sleep(2)


def _symlink_docx() -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        info = zipfile.ZipInfo("word/document.xml")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, b"../../outside")
    return stream.getvalue()


class UploadPreflightTests(unittest.TestCase):
    def setUp(self):
        self.policy = UploadSecurityPolicy(
            max_archive_entries=5000,
            max_archive_uncompressed_bytes=200 * 1024 * 1024,
            max_archive_compression_ratio=100,
        )

    def test_valid_text_pdf_and_ooxml_families_are_accepted(self):
        docx = _archive({
            "[Content_Types].xml": b"<Types/>",
            "word/document.xml": b"<document/>",
        })
        fixtures = (
            (b"# knowledge\nbody", "manual.md", "text/markdown"),
            (b"%PDF-1.7\nfixture", "manual.pdf", "application/pdf"),
            (
                docx,
                "manual.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
        )
        for payload, filename, media_type in fixtures:
            with self.subTest(filename=filename):
                report = validate_upload_content(
                    payload, filename, media_type, policy=self.policy
                )
                self.assertEqual(report["extension"], Path(filename).suffix)

    def test_specific_mime_and_magic_mismatch_fail_closed(self):
        for payload, filename, media_type, message in (
            (b"%PDF-1.7", "manual.pdf", "text/plain", "MIME"),
            (b"not-a-pdf", "manual.pdf", "application/pdf", "Magic"),
            (b"plain text", "manual.docx", "application/octet-stream", "Magic"),
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    validate_upload_content(
                        payload, filename, media_type, policy=self.policy
                    )

    def test_malformed_json_xml_and_binary_text_fail_closed(self):
        fixtures = (
            (b"{broken", "bad.json", "结构无效"),
            (b"<!DOCTYPE x><x/>", "bad.xml", "DOCTYPE"),
            (b"hello\x00world", "bad.txt", "二进制空字节"),
        )
        for payload, filename, message in fixtures:
            with self.subTest(filename=filename):
                with self.assertRaisesRegex(ValueError, message):
                    validate_upload_content(
                        payload, filename, "application/octet-stream", policy=self.policy
                    )

    def test_archive_traversal_duplicate_family_and_bomb_limits_fail_closed(self):
        fixtures = (
            (
                _archive({"[Content_Types].xml": b"<Types/>", "../word/x.xml": b"x"}),
                "bad.docx",
                self.policy,
                "路径穿越",
            ),
            (
                _archive({"[Content_Types].xml": b"<Types/>", "ppt/x.xml": b"x"}),
                "bad.docx",
                self.policy,
                "内容家族不匹配",
            ),
            (
                _archive({"a": b"x", "b": b"y"}),
                "bad.epub",
                UploadSecurityPolicy(max_archive_entries=1),
                "条目数",
            ),
            (
                _symlink_docx(),
                "bad.docx",
                self.policy,
                "符号链接",
            ),
            (
                _archive(
                    {"[Content_Types].xml": b"<Types/>", "word/x": b"0" * 10000},
                    compression=zipfile.ZIP_DEFLATED,
                ),
                "bad.docx",
                UploadSecurityPolicy(max_archive_compression_ratio=10),
                "压缩比",
            ),
        )
        for payload, filename, policy, message in fixtures:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    validate_upload_content(
                        payload, filename, "application/octet-stream", policy=policy
                    )


class ParserIsolationTests(unittest.TestCase):
    def _payload(self):
        return {
            "file_bytes": b"# Title\n\nKnowledge body.",
            "filename": "manual.md",
            "document_id": None,
            "source": None,
            "chunk_tokens": 500,
            "overlap_tokens": 50,
            "ingestion_schema_version": "test-v1",
            "ocr_min_chars": 200,
            "ocr_min_chars_per_page": 20,
            "ocr": {"enabled": False},
        }

    def test_document_is_parsed_in_spawned_process(self):
        parser = ProcessIsolatedDocumentParser(wall_timeout_seconds=20)
        result = parser.prepare(**self._payload())

        self.assertEqual(result["filename"], "manual.md")
        self.assertTrue(result["markdown"])
        self.assertGreater(len(result["chunks"]), 0)

    def test_wall_timeout_terminates_worker(self):
        parser = ProcessIsolatedDocumentParser(
            wall_timeout_seconds=0.1,
            worker_target=_slow_parser_worker,
        )
        started = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, "安全上限"):
            parser.prepare(**self._payload())
        self.assertLess(time.monotonic() - started, 1.5)

    def test_service_semaphore_serializes_parser_processes(self):
        class CountingParser:
            def __init__(self):
                self.active = 0
                self.maximum = 0

            def prepare(self, **payload):
                del payload
                self.active += 1
                self.maximum = max(self.maximum, self.active)
                time.sleep(0.05)
                self.active -= 1
                return {"ok": True}

        service = object.__new__(KnowledgeService)
        service.parser_isolation_enabled = True
        service.isolated_document_parser = CountingParser()
        service._parser_semaphore = asyncio.Semaphore(1)
        service._isolated_parser_payload = lambda *args, **kwargs: {
            "file_bytes": b"x"
        }

        async def run():
            return await asyncio.gather(*[
                service.prepare_document_async(b"x", "manual.md")
                for _ in range(4)
            ])

        asyncio.run(run())
        self.assertEqual(service.isolated_document_parser.maximum, 1)


class ClamAvContractTests(unittest.TestCase):
    def _scanner(self, folder: str) -> LocalClamAvScanner:
        executable = Path(folder) / "clamscan"
        executable.write_text("fixture", encoding="utf-8")
        executable.chmod(0o700)
        database = Path(folder) / "db"
        database.mkdir()
        (database / "daily.cvd").write_bytes(b"fixture")
        return LocalClamAvScanner(
            str(executable), database_path=str(database), expected_version="1.5.4"
        )

    def test_clean_scan_requires_fixed_version_and_fresh_daily_database(self):
        with tempfile.TemporaryDirectory() as folder:
            scanner = self._scanner(folder)
            with mock.patch(
                "services.rag.document.security.subprocess.run",
                side_effect=[
                    SimpleNamespace(stdout="ClamAV 1.5.4/fixture", returncode=0),
                    SimpleNamespace(stdout="", returncode=0),
                ],
            ):
                result = scanner.scan(b"clean", "manual.txt")
            self.assertTrue(result["clean"])

    def test_infected_version_mismatch_and_stale_database_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            scanner = self._scanner(folder)
            with mock.patch(
                "services.rag.document.security.subprocess.run",
                side_effect=[
                    SimpleNamespace(stdout="ClamAV 1.5.4/fixture", returncode=0),
                    SimpleNamespace(stdout="", returncode=1),
                ],
            ):
                with self.assertRaisesRegex(ValueError, "未通过"):
                    scanner.scan(b"infected", "manual.txt")

            with mock.patch(
                "services.rag.document.security.subprocess.run",
                return_value=SimpleNamespace(stdout="ClamAV 1.5.3", returncode=0),
            ):
                with self.assertRaisesRegex(RuntimeError, "版本"):
                    scanner.scan(b"clean", "manual.txt")

            daily = Path(folder) / "db" / "daily.cvd"
            stale = time.time() - 90000
            os.utime(daily, (stale, stale))
            with mock.patch(
                "services.rag.document.security.subprocess.run",
                return_value=SimpleNamespace(stdout="ClamAV 1.5.4", returncode=0),
            ):
                with self.assertRaisesRegex(RuntimeError, "更新时间"):
                    scanner.scan(b"clean", "manual.txt")


if __name__ == "__main__":
    unittest.main()
