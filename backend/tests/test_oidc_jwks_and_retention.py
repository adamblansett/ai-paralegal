import base64
import hashlib
import hmac
import json
import math
import random
from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from app.core.config import clear_settings_cache
from app.core.oidc import clear_oidc_jwks_cache
from tests.conftest import register_and_login


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _int_to_b64url(val: int) -> str:
    length = max(1, (val.bit_length() + 7) // 8)
    return _b64url_encode(val.to_bytes(length, "big"))


def _is_probable_prime(n: int, rng: random.Random) -> bool:
    if n < 2:
        return False
    small_primes = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)
    for p in small_primes:
        if n == p:
            return True
        if n % p == 0:
            return False
    d = n - 1
    s = 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for _ in range(16):
        a = rng.randrange(2, n - 1)
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = (x * x) % n
            if x == n - 1:
                break
        else:
            return False
    return True


def _next_prime(bits: int, seed: int) -> int:
    rng = random.Random(seed)
    while True:
        candidate = rng.getrandbits(bits) | (1 << (bits - 1)) | 1
        if _is_probable_prime(candidate, rng):
            return candidate


def _generate_rsa_test_keypair(kid: str, seed_offset: int) -> tuple[dict[str, Any], int, int]:
    """Generate an ephemeral RSA-1024 keypair for OIDC JWKS signature tests (test-only)."""
    p = _next_prime(512, 100003 + seed_offset)
    q = _next_prime(512, 200009 + seed_offset)
    while q == p:
        q = _next_prime(512, 300017 + seed_offset)
    n = p * q
    phi = (p - 1) * (q - 1)
    e = 65537
    assert math.gcd(e, phi) == 1
    d = pow(e, -1, phi)
    jwk = {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": kid,
        "n": _int_to_b64url(n),
        "e": _int_to_b64url(e),
    }
    return jwk, d, n


def _sign_rs256_jwt(
    *,
    header: dict[str, Any],
    claims: dict[str, Any],
    d: int,
    n: int,
) -> str:
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")

    k = (n.bit_length() + 7) // 8
    der_sha256_prefix = bytes.fromhex("3031300d060960864801650304020105000420")
    digest_info = der_sha256_prefix + hashlib.sha256(signing_input).digest()
    ps_len = k - len(digest_info) - 3
    em = b"\x00\x01" + (b"\xff" * ps_len) + b"\x00" + digest_info
    sig_int = pow(int.from_bytes(em, "big"), d, n)
    sig_b64 = _b64url_encode(sig_int.to_bytes(k, "big"))
    return f"{header_b64}.{payload_b64}.{sig_b64}"


async def test_oidc_jwks_rs256_verification_key_rotation_and_role_sync(
    raw_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jwk1, d1, n1 = _generate_rsa_test_keypair("kid-1", seed_offset=11)
    jwk2, d2, n2 = _generate_rsa_test_keypair("kid-2", seed_offset=79)
    active_keys: list[dict[str, Any]] = [jwk1]

    def idp_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": "https://idp.example.com",
                    "jwks_uri": "https://idp.example.com/.well-known/jwks.json",
                    "userinfo_endpoint": "https://idp.example.com/userinfo",
                },
            )
        if request.url.path == "/.well-known/jwks.json":
            return httpx.Response(200, json={"keys": active_keys})
        return httpx.Response(404)

    transport = httpx.MockTransport(idp_handler)
    real_async_client = httpx.AsyncClient

    def mock_oidc_http_client(**kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(**kwargs)

    monkeypatch.setattr("app.core.oidc.httpx.AsyncClient", mock_oidc_http_client)
    monkeypatch.setenv("OIDC_ENABLED", "true")
    monkeypatch.setenv("OIDC_ISSUER_URL", "https://idp.example.com")
    monkeypatch.setenv("OIDC_CLIENT_ID", "ai-paralegal-web")
    clear_settings_cache()
    clear_oidc_jwks_cache()

    try:
        future_exp = 4_102_444_800  # 2100-01-01T00:00:00Z
        valid_token_1 = _sign_rs256_jwt(
            header={"alg": "RS256", "typ": "JWT", "kid": "kid-1"},
            claims={
                "iss": "https://idp.example.com",
                "aud": "ai-paralegal-web",
                "sub": "sso-user-1",
                "email": "sso.counsel@firm.example.com",
                "name": "Morgan SSO",
                "groups": ["legal-associates"],
                "nonce": "nonce-abc",
                "exp": future_exp,
            },
            d=d1,
            n=n1,
        )

        ok_resp = await raw_client.post(
            "/api/v1/auth/oidc/exchange",
            json={"id_token": valid_token_1, "nonce": "nonce-abc"},
        )
        assert ok_resp.status_code == 200
        assert ok_resp.json()["user"]["role"] == "associate"

        # Tampered payload fails cryptographic RS256 verification
        h, p, s = valid_token_1.split(".")
        tampered_claims = _b64url_encode(
            json.dumps(
                {
                    "iss": "https://idp.example.com",
                    "aud": "ai-paralegal-web",
                    "email": "sso.counsel@firm.example.com",
                    "groups": ["legal-partners"],
                    "exp": future_exp,
                }
            ).encode("utf-8")
        )
        bad_sig_resp = await raw_client.post(
            "/api/v1/auth/oidc/exchange",
            json={"id_token": f"{h}.{tampered_claims}.{s}"},
        )
        assert bad_sig_resp.status_code == 401

        # Disallow alg=none attack
        none_hdr = _b64url_encode(b'{"alg":"none","typ":"JWT"}')
        none_resp = await raw_client.post(
            "/api/v1/auth/oidc/exchange",
            json={"id_token": f"{none_hdr}.{p}."},
        )
        assert none_resp.status_code == 401

        # Reject expired token, wrong audience, and wrong nonce
        expired_tok = _sign_rs256_jwt(
            header={"alg": "RS256", "kid": "kid-1"},
            claims={
                "iss": "https://idp.example.com",
                "aud": "ai-paralegal-web",
                "email": "sso.counsel@firm.example.com",
                "exp": 1000,
            },
            d=d1,
            n=n1,
        )
        assert (
            await raw_client.post("/api/v1/auth/oidc/exchange", json={"id_token": expired_tok})
        ).status_code == 401

        wrong_aud = _sign_rs256_jwt(
            header={"alg": "RS256", "kid": "kid-1"},
            claims={
                "iss": "https://idp.example.com",
                "aud": "different-client",
                "email": "sso.counsel@firm.example.com",
                "exp": future_exp,
            },
            d=d1,
            n=n1,
        )
        assert (
            await raw_client.post("/api/v1/auth/oidc/exchange", json={"id_token": wrong_aud})
        ).status_code == 401

        assert (
            await raw_client.post(
                "/api/v1/auth/oidc/exchange",
                json={"id_token": valid_token_1, "nonce": "wrong-nonce"},
            )
        ).status_code == 401

        # Rotate IdP key to kid-2 and promote user -> tests JWKS cache refresh & role sync
        active_keys = [jwk2]
        rotated_token = _sign_rs256_jwt(
            header={"alg": "RS256", "typ": "JWT", "kid": "kid-2"},
            claims={
                "iss": "https://idp.example.com",
                "aud": "ai-paralegal-web",
                "sub": "sso-user-1",
                "email": "sso.counsel@firm.example.com",
                "name": "Morgan Partner",
                "groups": ["legal-partners"],
                "exp": future_exp,
            },
            d=d2,
            n=n2,
        )
        sync_resp = await raw_client.post(
            "/api/v1/auth/oidc/exchange",
            json={"id_token": rotated_token},
        )
        assert sync_resp.status_code == 200
        assert sync_resp.json()["user"]["role"] == "partner"
        assert sync_resp.json()["user"]["full_name"] == "Morgan Partner"
    finally:
        clear_settings_cache()
        clear_oidc_jwks_cache()


async def test_retention_policy_legal_hold_skip_purge_and_admin_controls(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_resp = await async_client.get("/api/v1/admin/organization")
    assert org_resp.status_code == 200
    assert org_resp.json()["retention_days"] == 2555

    org_upd = await async_client.patch(
        "/api/v1/admin/organization",
        json={"retention_days": 365},
    )
    assert org_upd.status_code == 200
    assert org_upd.json()["retention_days"] == 365

    # Prevent accidental demotion of the last active partner
    me_resp = await async_client.get("/api/v1/auth/me")
    assert me_resp.status_code == 200
    me_id = me_resp.json()["id"]
    demote_resp = await async_client.patch(
        f"/api/v1/admin/users/{me_id}",
        json={"role": "associate"},
    )
    assert demote_resp.status_code == 409

    # Register another associate and confirm partner can promote/update them
    _other_tok, other_user = await register_and_login(
        async_client,
        email="junior@firm.example.com",
        full_name="Junior Counsel",
        role="paralegal",
    )
    other_id = other_user["id"]
    promote_resp = await async_client.patch(
        f"/api/v1/admin/users/{other_id}",
        json={"role": "associate"},
    )
    assert promote_resp.status_code == 200
    assert promote_resp.json()["role"] == "associate"

    # Webhook PATCH toggle test
    wh_resp = await async_client.post(
        "/api/v1/integrations/webhooks",
        json={
            "name": "Compliance Events",
            "channel_type": "webhook",
            "target_url": "https://compliance.example.com/hook",
            "signing_secret": "compliance-secret-123",
        },
    )
    assert wh_resp.status_code == 201
    wh_id = wh_resp.json()["id"]
    pause_resp = await async_client.patch(
        f"/api/v1/integrations/webhooks/{wh_id}",
        json={"is_active": False},
    )
    assert pause_resp.status_code == 200
    assert pause_resp.json()["is_active"] is False

    # Create two matters: unheld (Matter A) and legal-hold protected (Matter B)
    m_a = (
        await async_client.post(
            "/api/v1/matters",
            json={
                "matter_number": "RET-A-001",
                "title": "Expired Matter A",
                "client_name": "Client A",
            },
        )
    ).json()["id"]
    m_b = (
        await async_client.post(
            "/api/v1/matters",
            json={
                "matter_number": "RET-B-002",
                "title": "Preserved Matter B",
                "client_name": "Client B",
            },
        )
    ).json()["id"]

    hold_resp = await async_client.post(
        f"/api/v1/matters/{m_b}/legal-hold",
        json={"active": True, "reason": "Subpoena preservation order"},
    )
    assert hold_resp.status_code == 200

    doc_a = (
        await async_client.post(
            f"/api/v1/matters/{m_a}/documents",
            files={"file": ("expired_unheld.txt", b"Old agreement bytes", "text/plain")},
            data={"document_type": "contract"},
        )
    ).json()["document"]["id"]
    doc_b = (
        await async_client.post(
            f"/api/v1/matters/{m_b}/documents",
            files={"file": ("expired_held.txt", b"Preserved contract bytes", "text/plain")},
            data={"document_type": "contract"},
        )
    ).json()["document"]["id"]

    # Mark both documents expired in 2020
    assert (
        await async_client.patch(
            f"/api/v1/documents/{doc_a}/retention",
            json={"retention_until": "2020-01-01T00:00:00Z"},
        )
    ).status_code == 200
    assert (
        await async_client.patch(
            f"/api/v1/documents/{doc_b}/retention",
            json={"retention_until": "2020-01-01T00:00:00Z"},
        )
    ).status_code == 200

    # Dry-run preview identifies both without deleting anything
    dry = await async_client.post(
        "/api/v1/admin/retention/sweep",
        json={"dry_run": True},
    )
    assert dry.status_code == 200
    dry_data = dry.json()
    assert dry_data["eligible_count"] == 1
    assert dry_data["skipped_legal_hold_count"] == 1
    assert dry_data["purged_count"] == 0
    assert (await async_client.get(f"/api/v1/documents/{doc_a}")).status_code == 200

    # Re-enable webhook & intercept outbound compliance delivery with HMAC verification
    await async_client.patch(f"/api/v1/integrations/webhooks/{wh_id}", json={"is_active": True})
    sent: list[dict[str, Any]] = []

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
            sent.append({"url": url, "content": content, "headers": headers})
            return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr("app.services.integrations.webhooks.validate_webhook_url", lambda u, **_: u)
    monkeypatch.setattr("app.services.integrations.webhooks.httpx.AsyncClient", _FakeWHClient)

    live_sweep = await async_client.post(
        "/api/v1/admin/retention/sweep",
        json={"dry_run": False},
    )
    assert live_sweep.status_code == 200
    live_data = live_sweep.json()
    assert live_data["purged_count"] == 1
    assert live_data["skipped_legal_hold_count"] == 1

    assert (await async_client.get(f"/api/v1/documents/{doc_a}")).status_code == 404
    assert (await async_client.get(f"/api/v1/documents/{doc_b}")).status_code == 200

    verify_a = (await async_client.get(f"/api/v1/matters/{m_a}/audit-logs/verify")).json()
    assert verify_a["verified"] is True
    logs_a = (await async_client.get(f"/api/v1/matters/{m_a}/audit-logs")).json()
    assert any(entry["action"] == "retention_purged" for entry in logs_a)

    assert len(sent) == 1
    sig_hdr = sent[0]["headers"]["X-AI-Paralegal-Signature"]
    expected_sig = (
        "sha256="
        + hmac.new(b"compliance-secret-123", sent[0]["content"], hashlib.sha256).hexdigest()
    )
    assert sig_hdr == expected_sig
