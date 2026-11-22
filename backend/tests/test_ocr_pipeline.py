from types import SimpleNamespace
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.core.config import clear_settings_cache
from app.services.ocr import OCRService
from tests.conftest import build_scanned_pdf_stub


async def test_scanned_pdf_flagged_when_ocr_disabled_and_extracted_with_mock_ocr(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matter = (
        await async_client.post(
            "/api/v1/matters",
            json={
                "title": "Scanned Discovery Exhibits",
                "client_name": "Meridian Legal Group LLP",
                "practice_area": "Litigation",
            },
        )
    ).json()
    matter_id = matter["id"]

    scanned_bytes = build_scanned_pdf_stub(
        "SECTION 7. LIMITATION OF LIABILITY — Total aggregate liability is $185,000."
    )

    # 1. With OCR disabled, document still indexes safely and marks ocr_required=True
    monkeypatch.setenv("OCR_BACKEND", "disabled")
    clear_settings_cache()

    resp_disabled = await async_client.post(
        f"/api/v1/matters/{matter_id}/documents",
        files={"file": ("scanned_exhibit.pdf", scanned_bytes, "application/pdf")},
        data={"document_type": "contract"},
    )
    assert resp_disabled.status_code == 202
    doc_disabled = resp_disabled.json()["document"]
    assert doc_disabled["ingestion_status"] == "indexed"
    assert doc_disabled["metadata_json"]["ocr_required"] is True
    assert doc_disabled["metadata_json"]["ocr_applied"] is False

    # 2. Switch to mock OCR backend and reprocess -> OCR text recovered into chunks
    monkeypatch.setenv("OCR_BACKEND", "mock")
    clear_settings_cache()

    reproc = await async_client.post(f"/api/v1/documents/{doc_disabled['id']}/reprocess")
    assert reproc.status_code == 200
    doc_ocr = reproc.json()["document"]
    assert doc_ocr["ingestion_status"] == "indexed"
    assert doc_ocr["metadata_json"]["ocr_applied"] is True
    assert doc_ocr["metadata_json"]["ocr_required"] is False
    assert doc_ocr["metadata_json"]["ocr_backend"] == "mock"
    assert doc_ocr["metadata_json"]["ocr_pages"] == [1]

    chunks = (await async_client.get(f"/api/v1/documents/{doc_disabled['id']}/chunks")).json()
    assert len(chunks) >= 1
    assert "$185,000" in chunks[0]["content"]
    assert chunks[0]["metadata_json"]["source"] == "ocr"


def test_tesseract_ocr_subprocess_adapter() -> None:
    service = OCRService(backend_mode="tesseract", min_chars_per_page=20)
    fake_page = SimpleNamespace(images=[SimpleNamespace(data=b"PNG_IMAGE_BYTES")])

    fake_proc = SimpleNamespace(
        returncode=0,
        stdout=b"Indemnification Clause recovered by Tesseract OCR.",
    )
    with (
        patch("app.services.ocr.shutil.which", return_value="/usr/bin/tesseract"),
        patch("app.services.ocr.subprocess.run", return_value=fake_proc),
    ):
        res = service.extract_pdf_page_text(fake_page, 1, b"%PDF-1.4")

    assert res.applied is True
    assert res.backend == "tesseract"
    assert "Tesseract OCR" in res.text
