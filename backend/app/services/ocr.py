import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from app.core.metrics import metrics_registry

OCRBackendMode = Literal["auto", "tesseract", "mock", "disabled"]
_OCR_MARKER_RE = re.compile(rb"%OCR_TEXT:\s*(.+)")


@dataclass(slots=True)
class OCRPageResult:
    """Result of attempting OCR extraction on a single PDF page."""

    text: str
    backend: str
    applied: bool
    warning: str | None = None


class OCRService:
    """Pluggable OCR pipeline for image-only / scanned discovery PDFs."""

    def __init__(
        self,
        backend_mode: OCRBackendMode = "auto",
        min_chars_per_page: int = 20,
    ) -> None:
        self.backend_mode = backend_mode
        self.min_chars_per_page = min_chars_per_page

    @staticmethod
    def is_tesseract_available() -> bool:
        return shutil.which("tesseract") is not None

    @staticmethod
    def is_pdftoppm_available() -> bool:
        return shutil.which("pdftoppm") is not None

    def extract_pdf_page_text(
        self,
        page: Any,
        page_number: int,
        raw_pdf_bytes: bytes,
    ) -> OCRPageResult:
        """Attempt OCR extraction for a PDF page lacking an embedded text layer."""
        if self.backend_mode == "disabled":
            metrics_registry.record_ocr_page("disabled", False)
            return OCRPageResult(
                text="",
                backend="disabled",
                applied=False,
                warning=f"Scanned page {page_number} skipped because OCR_BACKEND=disabled.",
            )

        if self.backend_mode == "mock":
            marker_texts = [
                m.group(1).decode("utf-8", errors="replace").strip()
                for m in _OCR_MARKER_RE.finditer(raw_pdf_bytes)
            ]
            if marker_texts:
                idx = min(page_number - 1, len(marker_texts) - 1)
                recovered = marker_texts[idx]
            else:
                recovered = (
                    f"Scanned page {page_number} OCR transcript recovered by mock OCR backend."
                )
            metrics_registry.record_ocr_page("mock", True)
            return OCRPageResult(text=recovered, backend="mock", applied=True)

        if not self.is_tesseract_available():
            metrics_registry.record_ocr_page("unavailable", False)
            return OCRPageResult(
                text="",
                backend="unavailable",
                applied=False,
                warning=(
                    f"Scanned page {page_number} detected with no embedded text layer; "
                    "install tesseract-ocr to enable automated OCR."
                ),
            )

        result = self._run_tesseract_on_page(page, page_number, raw_pdf_bytes)
        metrics_registry.record_ocr_page(result.backend, result.applied)
        return result

    def _run_tesseract_on_page(
        self,
        page: Any,
        page_number: int,
        raw_pdf_bytes: bytes,
    ) -> OCRPageResult:
        extracted_blocks: list[str] = []

        # 1. Try embedded raster images on the PDF page first (via pypdf page.images)
        images = getattr(page, "images", None) or []
        for img in images:
            img_data = getattr(img, "data", None)
            if not img_data:
                continue
            txt = self._ocr_image_bytes(bytes(img_data))
            if txt:
                extracted_blocks.append(txt)

        # 2. Fallback to rendering PDF page via pdftoppm when available
        if not extracted_blocks and self.is_pdftoppm_available():
            rendered = self._render_page_and_ocr(raw_pdf_bytes, page_number)
            if rendered:
                extracted_blocks.append(rendered)

        combined = "\n\n".join(b.strip() for b in extracted_blocks if b.strip())
        if combined:
            return OCRPageResult(text=combined, backend="tesseract", applied=True)

        return OCRPageResult(
            text="",
            backend="tesseract",
            applied=False,
            warning=(
                f"Tesseract OCR found no readable text on scanned page {page_number}."
            ),
        )

    def _ocr_image_bytes(self, image_bytes: bytes) -> str:
        proc = subprocess.run(
            ["tesseract", "stdin", "stdout", "--psm", "6"],
            input=image_bytes,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0:
            return ""
        return proc.stdout.decode("utf-8", errors="replace").strip()

    def _render_page_and_ocr(self, raw_pdf_bytes: bytes, page_number: int) -> str:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            pdf_file = tmp_path / "input.pdf"
            pdf_file.write_bytes(raw_pdf_bytes)
            prefix = tmp_path / "page"
            render = subprocess.run(
                [
                    "pdftoppm",
                    "-png",
                    "-f",
                    str(page_number),
                    "-l",
                    str(page_number),
                    str(pdf_file),
                    str(prefix),
                ],
                capture_output=True,
                timeout=30,
                check=False,
            )
            if render.returncode != 0:
                return ""
            parts: list[str] = []
            for png_path in sorted(tmp_path.glob("page-*.png")):
                txt = self._ocr_image_bytes(png_path.read_bytes())
                if txt:
                    parts.append(txt)
            return "\n\n".join(parts)
