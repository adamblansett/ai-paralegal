import pytest
from httpx import AsyncClient


async def test_upload_extension_size_and_magic_byte_hardening(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matter_resp = await async_client.post(
        "/api/v1/matters",
        json={
            "title": "Upload Hardening Test",
            "client_name": "CyberShield",
            "practice_area": "Security",
        },
    )
    assert matter_resp.status_code == 201
    matter_id = matter_resp.json()["id"]

    # 1. Blocked extension -> 415
    bad_ext = await async_client.post(
        f"/api/v1/matters/{matter_id}/documents",
        files={"file": ("malware.exe", b"MZ\x90\x00", "application/octet-stream")},
    )
    assert bad_ext.status_code == 415

    # 2. Spoofed PDF signature -> 400
    bad_pdf = await async_client.post(
        f"/api/v1/matters/{matter_id}/documents",
        files={"file": ("fake.pdf", b"not actually a pdf", "application/pdf")},
    )
    assert bad_pdf.status_code == 400
    assert "Invalid PDF signature" in bad_pdf.json()["detail"]

    # 3. Size limit -> 413
    from app.core.config import clear_settings_cache

    monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "1")
    clear_settings_cache()
    oversized = b"A" * (1024 * 1024 + 64)
    too_big = await async_client.post(
        f"/api/v1/matters/{matter_id}/documents",
        files={"file": ("huge.txt", oversized, "text/plain")},
    )
    assert too_big.status_code == 413
    monkeypatch.delenv("MAX_UPLOAD_SIZE_MB", raising=False)
    clear_settings_cache()


async def test_refresh_token_rotation_replay_detection_logout_and_oidc_stub(
    raw_client: AsyncClient,
) -> None:
    # Login gives access + refresh token
    login_resp = await raw_client.post(
        "/api/v1/auth/login",
        json={"email": "partner@firm.example.com", "password": "ParalegalDemo2026!"},
    )
    assert login_resp.status_code == 200
    tokens = login_resp.json()
    access1 = tokens["access_token"]
    refresh1 = tokens["refresh_token"]
    assert refresh1

    # Rotate refresh token
    rot_resp = await raw_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh1},
    )
    assert rot_resp.status_code == 200
    tokens2 = rot_resp.json()
    refresh2 = tokens2["refresh_token"]
    assert refresh2 != refresh1

    # Replaying consumed refresh1 must fail & invalidate refresh2 too!
    replay_resp = await raw_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh1},
    )
    assert replay_resp.status_code == 401
    assert "reuse detected" in replay_resp.json()["detail"].lower()

    after_replay = await raw_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh2},
    )
    assert after_replay.status_code == 401

    # Logout immediately revokes JWT access token via jti denylist
    logout_resp = await raw_client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {access1}"},
        json={},
    )
    assert logout_resp.status_code == 200

    me_after_logout = await raw_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {access1}"},
    )
    assert me_after_logout.status_code == 401

    # OIDC discovery & disabled exchange endpoint
    prov_resp = await raw_client.get("/api/v1/auth/providers")
    assert prov_resp.status_code == 200
    assert prov_resp.json()["oidc_enabled"] is False

    oidc_resp = await raw_client.post(
        "/api/v1/auth/oidc/exchange",
        json={"id_token": "eyJhbGciOiJSUzI1NiJ9.stub.sig"},
    )
    assert oidc_resp.status_code == 501


async def test_rate_limiting_security_headers_and_prometheus_metrics(
    raw_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import clear_settings_cache

    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_AUTH_PER_MINUTE", "2")
    clear_settings_cache()

    for _ in range(2):
        r = await raw_client.post(
            "/api/v1/auth/login",
            json={"email": "partner@firm.example.com", "password": "ParalegalDemo2026!"},
        )
        assert r.status_code == 200
        # Security headers & request correlation ID
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert "x-request-id" in r.headers

    limited = await raw_client.post(
        "/api/v1/auth/login",
        json={"email": "partner@firm.example.com", "password": "ParalegalDemo2026!"},
        headers={"X-Request-ID": "custom-trace-123"},
    )
    assert limited.status_code == 429
    assert "retry-after" in limited.headers
    assert limited.headers["x-request-id"] == "custom-trace-123"

    monkeypatch.delenv("RATE_LIMIT_AUTH_PER_MINUTE", raising=False)
    clear_settings_cache()

    # Prometheus metrics endpoint + telemetry summary
    metrics_resp = await raw_client.get("/metrics")
    assert metrics_resp.status_code == 200
    assert "ai_paralegal_http_requests_total" in metrics_resp.text
    assert "ai_paralegal_llm_calls_total" in metrics_resp.text
