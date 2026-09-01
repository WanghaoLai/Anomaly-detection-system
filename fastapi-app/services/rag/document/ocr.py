"""Local-only PDF OCR fallback used by the ingestion boundary."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


_PDFINFO_PAGES_RE = re.compile(r"^Pages:\s*(\d+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class PdfOcrResult:
    text: str
    page_count: int
    ocr_pages: int


class LocalTesseractPdfOcr:
    """Render a PDF with Poppler and OCR every rendered page locally."""

    engine = "tesseract"
    engine_version = "5.5.3"
    model_family = "tessdata_fast"
    model_version = "4.1.0"
    model_sha256 = {
        "eng": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
        "chi_sim": "a5fcb6f0db1e1d6d8522f39db4e848f05984669172e584e8d76b6b3141e1f730",
    }

    def __init__(
        self,
        *,
        tesseract_path: str,
        pdftoppm_path: str,
        pdfinfo_path: str,
        tessdata_path: str | Path,
        languages: str = "chi_sim+eng",
        dpi: int = 300,
        timeout_seconds: float = 120.0,
    ) -> None:
        self.tesseract_path = str(tesseract_path)
        self.pdftoppm_path = str(pdftoppm_path)
        self.pdfinfo_path = str(pdfinfo_path)
        self.tessdata_path = Path(tessdata_path)
        self.languages = str(languages)
        self.dpi = int(dpi)
        self.timeout_seconds = float(timeout_seconds)
        if self.dpi <= 0 or self.timeout_seconds <= 0:
            raise ValueError("OCR dpi 与 timeout_seconds 必须大于 0")

    def _environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        environment["TESSDATA_PREFIX"] = str(self.tessdata_path)
        return environment

    def validate_runtime(self) -> None:
        for path, label in (
            (self.tesseract_path, "tesseract"),
            (self.pdftoppm_path, "pdftoppm"),
            (self.pdfinfo_path, "pdfinfo"),
        ):
            if not Path(path).is_file() or not os.access(path, os.X_OK):
                raise RuntimeError(f"本地 OCR 运行时不可用: {label}={path}")
        version = subprocess.run(
            [self.tesseract_path, "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
        ).stdout.splitlines()
        if not version or version[0].strip() != f"tesseract {self.engine_version}":
            actual = version[0].strip() if version else "unknown"
            raise RuntimeError(
                f"本地 OCR 版本不一致: expected={self.engine_version}, actual={actual}"
            )
        for language in self.languages.split("+"):
            model = self.tessdata_path / f"{language}.traineddata"
            if not model.is_file() or model.stat().st_size <= 0:
                raise RuntimeError(f"本地 OCR 模型缺失: {model}")
            expected_hash = self.model_sha256.get(language)
            if expected_hash is None:
                raise RuntimeError(f"OCR 语言模型未固定 SHA-256: {language}")
            digest = hashlib.sha256(model.read_bytes()).hexdigest()
            if digest != expected_hash:
                raise RuntimeError(
                    f"OCR 语言模型 SHA-256 不一致: language={language}"
                )

    def page_count(self, file_bytes: bytes) -> int:
        with tempfile.TemporaryDirectory(prefix="rag-pdf-inspect-") as folder:
            # macOS 的 /tmp 是符号链接；Leptonica 在部分构建中无法沿该路径读图。
            root = Path(folder).resolve()
            pdf_path = root / "source.pdf"
            pdf_path.write_bytes(bytes(file_bytes))
            result = subprocess.run(
                [self.pdfinfo_path, str(pdf_path)],
                check=True,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        match = _PDFINFO_PAGES_RE.search(result.stdout)
        if match is None or int(match.group(1)) <= 0:
            raise RuntimeError("无法取得 PDF 页数")
        return int(match.group(1))

    def extract(self, file_bytes: bytes) -> PdfOcrResult:
        self.validate_runtime()
        with tempfile.TemporaryDirectory(prefix="rag-pdf-ocr-") as folder:
            root = Path(folder).resolve()
            pdf_path = root / "source.pdf"
            page_prefix = root / "page"
            pdf_path.write_bytes(bytes(file_bytes))
            subprocess.run(
                [
                    self.pdftoppm_path,
                    "-png",
                    "-r",
                    str(self.dpi),
                    str(pdf_path),
                    str(page_prefix),
                ],
                check=True,
                capture_output=True,
                timeout=self.timeout_seconds,
            )
            images = sorted(root.glob("page-*.png"))
            if not images:
                raise RuntimeError("PDF 未渲染出任何 OCR 页面")
            page_texts: list[str] = []
            for image_path in images:
                result = subprocess.run(
                    [
                        self.tesseract_path,
                        str(image_path),
                        "stdout",
                        "-l",
                        self.languages,
                        "--oem",
                        "1",
                        "--psm",
                        "6",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    env=self._environment(),
                )
                page_texts.append(result.stdout.strip())
        text = "\n\f\n".join(page_texts).strip()
        return PdfOcrResult(
            text=text,
            page_count=len(images),
            ocr_pages=len(images),
        )


__all__ = ["LocalTesseractPdfOcr", "PdfOcrResult"]
