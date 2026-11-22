import io
import zipfile
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import AsyncClient

from app.core.config import Settings
from app.llm.providers.base import ProviderError
from app.llm.router import ModelRouter
from app.llm.types import (
    LLMRequest,
    ResearchAnswerLLMOutput,
    SourceChunkContext,
)
from app.services.guardrails import (
    detect_privilege_indicators,
    redact_pii_for_cloud,
    scan_text_for_pii,
)


def test_pii_detection_and_cloud_redaction() -> None:
    text = (
        "Contact John Doe at john.doe@example.com or 415-555-0199. "
        "Employee SSN: 123-45-6789. Contract cap is $240,000 over 12 months."
    )
    scan = scan_text_for_pii(text)
    assert scan.total_count == 3
    assert scan.counts_by_type == {"email": 1, "phone": 1, "ssn": 1}

    redacted, counts = redact_pii_for_cloud(text)
    assert "john.doe@example.com" not in redacted
    assert "415-555-0199" not in redacted
    assert "123-45-6789" not in redacted
    assert "[REDACTED_SSN_1]" in redacted
    assert "[REDACTED_EMAIL_1]" in redacted
    assert "[REDACTED_PHONE_1]" in redacted
    assert "$240,000" in redacted
    assert counts == {"email": 1, "phone": 1, "ssn": 1}

    priv = detect_privilege_indicators(
        "PRIVILEGED AND CONFIDENTIAL\nATTORNEY-CLIENT PRIVILEGED COMMUNICATION\nStrategy memo."
    )
    assert priv.suggested_status == "privileged_and_confidential"
    assert priv.confidence >= 0.85


async def test_outbound_cloud_guardrail_blocks_privileged_and_redacts_pii() -> None:
    settings = Settings(
        llm_default_provider="gemini",
        llm_fallback_providers="mock",
        gemini_api_key="test-key",
        llm_redact_pii_for_cloud=True,
        llm_block_privileged_for_cloud=True,
    )
    router = ModelRouter(settings)

    # 1. Privileged chunks are blocked from Gemini and fall back to local mock
    priv_chunk = SourceChunkContext(
        chunk_ref="C1",
        chunk_id="chunk-priv",
        document_id="doc-priv",
        document_filename="privileged_strategy_memo.txt",
        chunk_index=0,
        privilege_status="attorney_client",
        content="Attorney-client litigation strategy regarding termination notice.",
    )
    priv_req = LLMRequest(
        task_kind="qa_research",
        prompt_template="research_qa_v1",
        prompt_version="1.0.0",
        system_prompt="Be grounded.",
        user_prompt="What is the strategy?",
        context_chunks=[priv_chunk],
        metadata={"question": "What is the strategy?"},
    )
    with pytest.raises(ProviderError):
        await router.generate_structured(
            priv_req,
            ResearchAnswerLLMOutput,
            provider="gemini",
            enable_fallback=False,
        )

    fallback_resp = await router.generate_structured(
        priv_req,
        ResearchAnswerLLMOutput,
        provider="gemini",
        enable_fallback=True,
    )
    assert fallback_resp.provider == "mock"
    assert fallback_resp.fallback_used is True

    # 2. Non-privileged chunk containing PII is scrubbed before hitting Gemini API
    pii_chunk = SourceChunkContext(
        chunk_ref="C1",
        chunk_id="chunk-pii",
        document_id="doc-pii",
        document_filename="vendor_contact.txt",
        chunk_index=0,
        privilege_status="none",
        content="Primary contact: legal@vendor.test SSN 123-45-6789 Fee $185,000.",
    )
    pii_req = LLMRequest(
        task_kind="qa_research",
        prompt_template="research_qa_v1",
        prompt_version="1.0.0",
        system_prompt="Be grounded.",
        user_prompt="Source [C1]: Primary contact: legal@vendor.test SSN 123-45-6789 Fee $185,000.",
        context_chunks=[pii_chunk],
        metadata={"question": "What is the fee?"},
    )
    captured_payloads: list[dict[str, object]] = []

    async def fake_post(url: str, json: dict[str, object]) -> httpx.Response:
        captured_payloads.append(json)
        return httpx.Response(
            status_code=200,
            request=httpx.Request("POST", url),
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": (
                                        '{"answer": "Fee is $185,000.", "confidence": 0.9, '
                                        '"key_points": [], "uncertainties": [], '
                                        '"missing_context": [], "citations": []}'
                                    )
                                }
                            ]
                        }
                    }
                ],
                "usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 20},
            },
        )

    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=fake_post)):
        gem_resp = await router.generate_structured(
            pii_req,
            ResearchAnswerLLMOutput,
            provider="gemini",
            enable_fallback=False,
        )

    assert gem_resp.provider == "gemini"
    assert gem_resp.redaction_summary == {"email": 1, "ssn": 1}
    sent_text = str(captured_payloads[0])
    assert "legal@vendor.test" not in sent_text
    assert "123-45-6789" not in sent_text
    assert "[REDACTED_SSN_1]" in sent_text
    assert "[REDACTED_EMAIL_1]" in sent_text


async def test_privilege_detection_bates_stamping_and_privilege_log_exports(
    async_client: AsyncClient,
) -> None:
    matter_resp = await async_client.post(
        "/api/v1/matters",
        json={
            "title": "Privilege & Production Dispute",
            "client_name": "Meridian Corp",
            "practice_area": "Litigation",
        },
    )
    assert matter_resp.status_code == 201
    matter_id = matter_resp.json()["id"]

    # Doc 1: auto-detects privilege + PII
    doc1_resp = await async_client.post(
        f"/api/v1/matters/{matter_id}/documents",
        files={
            "file": (
                "counsel_advice.txt",
                (
                    b"PRIVILEGED AND CONFIDENTIAL\n"
                    b"ATTORNEY-CLIENT PRIVILEGED COMMUNICATION\n"
                    b"Counsel advised executive@meridian.test on SSN 987-65-4321 risk.\n"
                ),
                "text/plain",
            )
        },
        data={"document_type": "memo"},
    )
    assert doc1_resp.status_code == 202
    doc1 = doc1_resp.json()["document"]
    assert doc1["privilege_status"] == "privileged_and_confidential"
    assert doc1["pii_detected_count"] == 2
    assert doc1["pii_summary_json"] == {"email": 1, "ssn": 1}

    # Doc 2: public contract, manually marked work_product later
    doc2_resp = await async_client.post(
        f"/api/v1/matters/{matter_id}/documents",
        files={
            "file": (
                "vendor_contract.txt",
                b"SECTION 1. SERVICES\nStandard commercial agreement terms.\n",
                "text/plain",
            )
        },
        data={"document_type": "contract"},
    )
    assert doc2_resp.status_code == 202
    doc2 = doc2_resp.json()["document"]
    assert doc2["privilege_status"] == "none"

    priv_patch = await async_client.patch(
        f"/api/v1/documents/{doc2['id']}/privilege",
        json={
            "privilege_status": "work_product",
            "privilege_reason": "Annotated in anticipation of trial.",
        },
    )
    assert priv_patch.status_code == 200
    assert priv_patch.json()["privilege_status"] == "work_product"

    # Sequential Bates stamping
    bates_resp = await async_client.post(
        f"/api/v1/matters/{matter_id}/documents/bates-stamp",
        json={"prefix": "MERID", "start_number": 100, "zero_pad": 5},
    )
    assert bates_resp.status_code == 200
    bates_data = bates_resp.json()
    assert bates_data["stamped_count"] == 2
    assert bates_data["documents"][0]["bates_start"] == "MERID-00100"
    assert bates_data["documents"][1]["bates_start"] == "MERID-00101"

    chunks_resp = await async_client.get(f"/api/v1/documents/{doc1['id']}/chunks")
    assert chunks_resp.status_code == 200
    assert chunks_resp.json()[0]["metadata_json"]["bates_number"] == "MERID-00100"

    # Privilege log exports (CSV, JSON, Markdown, DOCX)
    csv_log = await async_client.get(
        f"/api/v1/matters/{matter_id}/exports/privilege-log?format=csv"
    )
    assert csv_log.status_code == 200
    assert "MERID-00100" in csv_log.text
    assert "privileged_and_confidential" in csv_log.text
    assert "work_product" in csv_log.text

    docx_log = await async_client.get(
        f"/api/v1/matters/{matter_id}/exports/privilege-log?format=docx"
    )
    assert docx_log.status_code == 200
    with zipfile.ZipFile(io.BytesIO(docx_log.content), "r") as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
        assert "MERID-00100" in xml
        assert "counsel_advice.txt" in xml
