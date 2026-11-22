import json
from unittest.mock import AsyncMock, patch

import httpx

from app.core.config import Settings
from app.llm.citations import compute_grounding_score, evaluate_escalation, verify_citations
from app.llm.json_utils import parse_structured_output
from app.llm.router import ModelRouter
from app.llm.types import (
    LLMRequest,
    RawCitation,
    ResearchAnswerLLMOutput,
    SourceChunkContext,
)


def test_deepseek_think_stripping_and_markdown_json_extraction() -> None:
    raw = (
        "<think>Let me verify the exact clause before responding.</think>\n"
        "```json\n"
        '{"answer": "Liability is capped at $20,000.", "confidence": 0.9, '
        '"key_points": ["Cap is 1 month fees"], "uncertainties": [], '
        '"missing_context": [], "citations": []}\n'
        "```"
    )
    parsed = parse_structured_output(raw, ResearchAnswerLLMOutput)
    assert parsed.answer == "Liability is capped at $20,000."
    assert parsed.confidence == 0.9


def test_citation_verification_and_escalation_logic() -> None:
    chunk_id = "11111111-1111-1111-1111-111111111111"
    chunk_map = {
        chunk_id: SourceChunkContext(
            chunk_ref="C1",
            chunk_id=chunk_id,
            document_id="22222222-2222-2222-2222-222222222222",
            document_filename="sample_msa.txt",
            document_type="contract",
            chunk_index=0,
            page_number=2,
            paragraph_index=7,
            section_heading="SECTION 7. LIMITATION OF LIABILITY",
            char_start=120,
            char_end=340,
            content=(
                "VENDOR'S TOTAL AGGREGATE LIABILITY ARISING OUT OF OR RELATED TO THIS AGREEMENT "
                "SHALL NOT EXCEED ONE (1) MONTH OF FEES PAID BY CLIENT ($20,000)."
            ),
        )
    }

    verified = verify_citations(
        [
            RawCitation(chunk_id=chunk_id, quote="SHALL NOT EXCEED ONE (1) MONTH OF FEES"),
            RawCitation(
                chunk_id=chunk_id,
                quote=(
                    "VENDOR TOTAL AGGREGATE LIABILITY ARISING OUT OF THIS AGREEMENT "
                    "SHALL NOT EXCEED ONE MONTH"
                ),
            ),
            RawCitation(chunk_id="99999999-9999-9999-9999-999999999999", quote="Fabricated quote"),
        ],
        chunk_map,
    )
    assert verified[0].verification_status == "verified_exact"
    assert verified[0].page_number == 2
    assert verified[0].paragraph_index == 7
    assert verified[1].verification_status == "verified_fuzzy"
    assert verified[2].verification_status == "unverified"

    score = compute_grounding_score(verified)
    assert 0.5 <= score <= 0.65

    settings = Settings(
        llm_confidence_threshold=0.75,
        llm_grounding_threshold=0.70,
        llm_escalate_high_risk=True,
    )
    escalate, reason = evaluate_escalation(
        confidence=0.65,
        grounding_score=score,
        severity="high",
        unverified_count=1,
        settings=settings,
    )
    assert escalate is True
    assert reason is not None
    assert "unverified citation" in reason


async def test_ollama_provider_gemini_provider_and_fallback_routing() -> None:
    chunk = SourceChunkContext(
        chunk_ref="C1",
        chunk_id="chunk-1",
        document_id="doc-1",
        document_filename="msa.txt",
        chunk_index=0,
        page_number=1,
        paragraph_index=1,
        content="Client shall pay Vendor an annual subscription fee of $240,000.",
    )
    req = LLMRequest(
        task_kind="qa_research",
        prompt_template="research_qa_v1",
        prompt_version="1.0.0",
        system_prompt="Be grounded.",
        user_prompt="What is the annual fee?",
        context_chunks=[chunk],
        metadata={"question": "What is the annual fee?"},
    )

    # 1. Mocked Ollama response (deepseek-r1 with <think> block)
    ollama_payload = {
        "message": {
            "content": "<think>checking</think>"
            + json.dumps(
                {
                    "answer": "Annual fee is $240,000.",
                    "confidence": 0.95,
                    "key_points": ["$240,000 annual fee"],
                    "uncertainties": [],
                    "missing_context": [],
                    "citations": [
                        {"chunk_id": "chunk-1", "quote": "annual subscription fee of $240,000"}
                    ],
                }
            )
        },
        "prompt_eval_count": 40,
        "eval_count": 25,
    }
    settings = Settings(
        llm_default_provider="ollama",
        llm_fallback_providers="mock",
        gemini_api_key="test-gemini-key",
    )
    router = ModelRouter(settings)

    mock_resp = httpx.Response(
        200,
        json=ollama_payload,
        request=httpx.Request("POST", "http://localhost:11434/api/chat"),
    )
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)):
        res = await router.generate_structured(
            req,
            ResearchAnswerLLMOutput,
            provider="ollama",
            model="deepseek-r1-distill-qwen-8b",
        )
    assert res.provider == "ollama"
    assert res.model_name == "deepseek-r1-distill-qwen-8b"
    assert res.data.answer == "Annual fee is $240,000."
    assert res.token_usage.total_tokens == 65
    assert res.fallback_used is False

    # 2. Mocked Gemini response
    gemini_payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": json.dumps(
                                {
                                    "answer": "Fee is $240,000 per year.",
                                    "confidence": 0.93,
                                    "key_points": [],
                                    "uncertainties": [],
                                    "missing_context": [],
                                    "citations": [
                                        {
                                            "chunk_id": "chunk-1",
                                            "quote": "annual subscription fee of $240,000",
                                        }
                                    ],
                                }
                            )
                        }
                    ]
                }
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 50,
            "candidatesTokenCount": 30,
            "totalTokenCount": 80,
        },
    }
    gemini_resp = httpx.Response(
        200,
        json=gemini_payload,
        request=httpx.Request("POST", "https://generativelanguage.googleapis.com"),
    )
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=gemini_resp)):
        g_res = await router.generate_structured(
            req,
            ResearchAnswerLLMOutput,
            provider="gemini",
            model="gemini-2.5-flash",
        )
    assert g_res.provider == "gemini"
    assert g_res.data.answer == "Fee is $240,000 per year."
    assert g_res.token_usage.total_tokens == 80

    # 3. Automatic fallback from unreachable Ollama to Mock
    with patch(
        "httpx.AsyncClient.post",
        new=AsyncMock(side_effect=httpx.ConnectError("Connection refused")),
    ):
        fb_res = await router.generate_structured(
            req,
            ResearchAnswerLLMOutput,
            provider="ollama",
            enable_fallback=True,
        )
    assert fb_res.provider == "mock"
    assert fb_res.fallback_used is True
    assert len(fb_res.fallback_errors) >= 1
