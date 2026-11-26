import json
from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from app.core.config import Settings, clear_settings_cache
from app.core.mfa import generate_totp_code
from app.llm.providers.gemini import GeminiProvider
from app.llm.providers.ollama import OllamaProvider
from app.llm.types import LLMRequest
from tests.conftest import register_and_login


async def test_document_version_comparison_conflict_check_ethical_wall_and_obligations(
    async_client: AsyncClient,
    raw_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1. Create initial matter with counterparty Apex Cloud for conflict screening
    m_resp = await async_client.post(
        "/api/v1/matters",
        json={
            "matter_number": "OPS-001",
            "title": "Acme MSA Negotiation & Deadlines",
            "client_name": "Apex Cloud Systems Inc.",
            "practice_area": "Commercial Contracts",
        },
    )
    assert m_resp.status_code == 201
    matter_id = m_resp.json()["id"]

    # Conflict screening matches normalized counterparty against existing tenant matter
    conf_resp = await async_client.post(
        "/api/v1/matters/conflict-check",
        json={
            "client_name": "New Enterprise Buyer",
            "counterparties": ["Apex Cloud Systems LLC"],
            "matter_title": "Buyer vs Apex Cloud Systems",
        },
    )
    assert conf_resp.status_code == 200
    conf_data = conf_resp.json()
    assert conf_data["has_conflicts"] is True
    assert conf_data["risk_level"] == "high"
    assert len(conf_data["matches"]) >= 1

    # 2. Upload v1 and v2 contract drafts and run version diff + risk regression check
    v1_bytes = (
        b"SECTION 1. INDEMNIFICATION.\n"
        b"Vendor and Customer agree to mutual indemnification for third-party claims.\n\n"
        b"SECTION 2. LIMITATION OF LIABILITY.\n"
        b"Each party's aggregate liability shall not exceed $500,000.\n\n"
        b"SECTION 3. GOVERNING LAW & CURE.\n"
        b"Governed by the laws of the State of Delaware. Counterparty has 30 days to cure breach.\n"
    )
    v2_bytes = (
        b"SECTION 1. INDEMNIFICATION.\n"
        b"Customer shall solely indemnify Vendor for all claims without consent restrictions.\n\n"
        b"SECTION 2. LIMITATION OF LIABILITY.\n"
        b"Vendor's aggregate liability shall not exceed $50,000.\n\n"
        b"SECTION 3. GOVERNING LAW & CURE.\n"
        b"Governed by the laws of California. Counterparty has 5 days to cure breach. "
        b"Term will auto-renew unless 90 days prior written notice is given.\n\n"
        b"SECTION 4. COMPLIANCE PAYMENT.\n"
        b"Annual compliance payment is due on 2020-01-15.\n"
    )
    d1 = (
        await async_client.post(
            f"/api/v1/matters/{matter_id}/documents",
            files={"file": ("msa_v1.txt", v1_bytes, "text/plain")},
            data={"document_type": "contract"},
        )
    ).json()["document"]["id"]
    d2 = (
        await async_client.post(
            f"/api/v1/matters/{matter_id}/documents",
            files={"file": ("msa_v2.txt", v2_bytes, "text/plain")},
            data={"document_type": "contract"},
        )
    ).json()["document"]["id"]

    diff_resp = await async_client.post(
        f"/api/v1/matters/{matter_id}/documents/compare",
        json={"base_document_id": d1, "revised_document_id": d2},
    )
    assert diff_resp.status_code == 200
    diff_data = diff_resp.json()
    assert diff_data["modified_count"] + diff_data["added_count"] >= 1
    reg_cats = {r["category"] for r in diff_data["risk_regressions"]}
    assert "limitation_of_liability" in reg_cats
    assert "indemnification" in reg_cats
    assert "governing_law" in reg_cats

    # 3. Ethical Wall blocks even another partner until lifted
    walled_tok, walled_user = await register_and_login(
        raw_client,
        email="walled.partner@firm.example.com",
        full_name="Conflicted Partner",
        role="partner",
    )
    walled_headers = {"Authorization": f"Bearer {walled_tok}"}

    wall_create = await async_client.post(
        f"/api/v1/matters/{matter_id}/ethical-walls",
        json={"user_id": walled_user["id"], "reason": "Prior representation of adverse party"},
    )
    assert wall_create.status_code == 201

    assert (
        await raw_client.get(f"/api/v1/matters/{matter_id}", headers=walled_headers)
    ).status_code == 403
    visible_ids = [
        m["id"] for m in (await raw_client.get("/api/v1/matters", headers=walled_headers)).json()
    ]
    assert matter_id not in visible_ids

    assert (
        await async_client.delete(f"/api/v1/matters/{matter_id}/ethical-walls/{walled_user['id']}")
    ).status_code == 204
    assert (
        await raw_client.get(f"/api/v1/matters/{matter_id}", headers=walled_headers)
    ).status_code == 200

    # 4. Extract obligations, export .ics calendar, and check deadline webhook alerts
    ext_resp = await async_client.post(f"/api/v1/matters/{matter_id}/obligations/extract")
    assert ext_resp.status_code == 200
    extracted = ext_resp.json()
    assert len(extracted) >= 2

    ics_resp = await async_client.get(f"/api/v1/matters/{matter_id}/obligations/calendar.ics")
    assert ics_resp.status_code == 200
    assert "BEGIN:VCALENDAR" in ics_resp.text
    assert "BEGIN:VEVENT" in ics_resp.text
    assert "TRIGGER:-P7D" in ics_resp.text

    sent_wh: list[dict[str, Any]] = []

    class _FakeWHClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "_FakeWHClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(
            self, url: str, *, content: bytes, headers: dict[str, str]
        ) -> httpx.Response:
            sent_wh.append({"url": url, "content": content, "headers": headers})
            return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr("app.services.integrations.webhooks.validate_webhook_url", lambda u, **_: u)
    monkeypatch.setattr("app.services.integrations.webhooks.httpx.AsyncClient", _FakeWHClient)

    wh = await async_client.post(
        "/api/v1/integrations/webhooks",
        json={
            "name": "Deadline Alerts",
            "channel_type": "slack",
            "target_url": "https://hooks.slack.example.com/services/T1/B1/K1",
            "events": ["obligation.deadline_alert"],
        },
    )
    assert wh.status_code == 201

    alert_resp = await async_client.post(
        f"/api/v1/matters/{matter_id}/obligations/check-alerts?due_within_days=30"
    )
    assert alert_resp.status_code == 200
    alert_data = alert_resp.json()
    assert alert_data["overdue_count"] >= 1
    assert alert_data["webhooks_dispatched"] == 1
    assert len(sent_wh) == 1

    # 5. SSE Streaming Research Q&A
    stream_resp = await async_client.post(
        f"/api/v1/matters/{matter_id}/research/query/stream",
        json={
            "question": "What is the aggregate liability cap and notice period?",
            "provider": "mock",
            "save_to_queue": True,
        },
    )
    assert stream_resp.status_code == 200
    assert "text/event-stream" in stream_resp.headers["content-type"]
    assert "event: retrieval" in stream_resp.text
    assert "event: token" in stream_resp.text
    assert "event: complete" in stream_resp.text


async def test_provider_streaming_ollama_and_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    req = LLMRequest(
        task_kind="qa_research",
        prompt_template="test",
        prompt_version="1",
        system_prompt="You are a legal assistant.",
        user_prompt="Summarize clause.",
    )

    ollama_ndjson = (
        json.dumps({"message": {"content": "Liability "}, "done": False}) + "\n"
        + json.dumps({"message": {"content": "is capped."}, "done": True}) + "\n"
    )
    ollama_transport = httpx.MockTransport(
        lambda _r: httpx.Response(200, content=ollama_ndjson.encode("utf-8"))
    )
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.llm.providers.ollama.httpx.AsyncClient",
        lambda **kw: real_client(transport=ollama_transport, **kw),
    )
    ollama_prov = OllamaProvider(Settings())
    ol_tokens = [tok async for tok in ollama_prov.stream_text(req)]
    assert "".join(ol_tokens) == "Liability is capped."

    gemini_sse = (
        'data: {"candidates":[{"content":{"parts":[{"text":"Vendor "}]}}]}\n\n'
        'data: {"candidates":[{"content":{"parts":[{"text":"cap applies."}]}}]}\n\n'
    )
    gem_transport = httpx.MockTransport(
        lambda _r: httpx.Response(200, content=gemini_sse.encode("utf-8"))
    )
    monkeypatch.setattr(
        "app.llm.providers.gemini.httpx.AsyncClient",
        lambda **kw: real_client(transport=gem_transport, **kw),
    )
    gem_prov = GeminiProvider(Settings(gemini_api_key="test-gemini-key"))
    gem_tokens = [tok async for tok in gem_prov.stream_text(req)]
    assert "".join(gem_tokens) == "Vendor cap applies."


async def test_totp_mfa_and_scim_provisioning(
    raw_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1. Enroll, verify, enforce on login, and disable TOTP MFA
    tok, user_info = await register_and_login(
        raw_client,
        email="mfa.user@firm.example.com",
        full_name="MFA Counsel",
        role="associate",
        password="StrongPassword99!",
    )
    headers = {"Authorization": f"Bearer {tok}"}

    enroll = await raw_client.post("/api/v1/auth/mfa/enroll", headers=headers)
    assert enroll.status_code == 200
    secret = enroll.json()["secret"]
    assert enroll.json()["otpauth_uri"].startswith("otpauth://totp/")

    code = generate_totp_code(secret)
    verify = await raw_client.post(
        "/api/v1/auth/mfa/verify",
        headers=headers,
        json={"code": code},
    )
    assert verify.status_code == 200
    assert verify.json()["mfa_enabled"] is True

    no_code_login = await raw_client.post(
        "/api/v1/auth/login",
        json={"email": user_info["email"], "password": "StrongPassword99!"},
    )
    assert no_code_login.status_code == 401
    assert no_code_login.headers.get("x-mfa-required") == "true"

    mfa_login = await raw_client.post(
        "/api/v1/auth/login",
        json={
            "email": user_info["email"],
            "password": "StrongPassword99!",
            "totp_code": generate_totp_code(secret),
        },
    )
    assert mfa_login.status_code == 200

    dis = await raw_client.post(
        "/api/v1/auth/mfa/disable",
        headers=headers,
        json={"code": generate_totp_code(secret)},
    )
    assert dis.status_code == 200
    assert dis.json()["mfa_enabled"] is False

    # 2. SCIM 2.0 Directory Provisioning + Offboarding
    assert (await raw_client.get("/api/v1/scim/v2/Users")).status_code == 501

    monkeypatch.setenv("SCIM_ENABLED", "true")
    monkeypatch.setenv("SCIM_BEARER_TOKEN", "scim-enterprise-token-32-bytes-ok")
    clear_settings_cache()
    try:
        scim_headers = {"Authorization": "Bearer scim-enterprise-token-32-bytes-ok"}
        assert (await raw_client.get("/api/v1/scim/v2/Users")).status_code == 401

        create_scim = await raw_client.post(
            "/api/v1/scim/v2/Users",
            headers=scim_headers,
            json={
                "externalId": "okta-001",
                "userName": "scim.counsel@firm.example.com",
                "displayName": "SCIM Counsel",
                "roles": [{"value": "paralegal"}],
                "active": True,
            },
        )
        assert create_scim.status_code == 201
        scim_id = create_scim.json()["id"]

        filt = await raw_client.get(
            '/api/v1/scim/v2/Users?filter=userName eq "scim.counsel@firm.example.com"',
            headers=scim_headers,
        )
        assert filt.status_code == 200
        assert filt.json()["totalResults"] == 1

        patch_scim = await raw_client.patch(
            f"/api/v1/scim/v2/Users/{scim_id}",
            headers=scim_headers,
            json={
                "Operations": [
                    {"op": "replace", "path": "roles", "value": [{"value": "associate"}]},
                    {"op": "replace", "path": "active", "value": False},
                ]
            },
        )
        assert patch_scim.status_code == 200
        assert patch_scim.json()["active"] is False
        assert patch_scim.json()["roles"][0]["value"] == "associate"

        del_scim = await raw_client.delete(
            f"/api/v1/scim/v2/Users/{scim_id}",
            headers=scim_headers,
        )
        assert del_scim.status_code == 204
    finally:
        clear_settings_cache()
