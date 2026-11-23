import base64
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import Settings

_RSA_DIGEST_INFO_PREFIXES: dict[str, tuple[Any, bytes]] = {
    "RS256": (hashlib.sha256, bytes.fromhex("3031300d060960864801650304020105000420")),
    "RS384": (hashlib.sha384, bytes.fromhex("3041300d060960864801650304020205000430")),
    "RS512": (hashlib.sha512, bytes.fromhex("3051300d060960864801650304020305000440")),
}
_SUPPORTED_RSA_ALGS: frozenset[str] = frozenset(_RSA_DIGEST_INFO_PREFIXES.keys())
_SUPPORTED_EC_ALGS: frozenset[str] = frozenset({"ES256", "ES384", "ES512"})
_CLOCK_LEEWAY_SECONDS = 30


class OIDCVerificationError(ValueError):
    """Raised when OIDC discovery, JWKS lookup, or ID token validation fails."""


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * ((4 - len(segment) % 4) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def _verify_rsa_pkcs1_v15(
    alg: str,
    signing_input: bytes,
    signature: bytes,
    jwk: dict[str, Any],
) -> bool:
    """Constant-time RFC 8017 RSASSA-PKCS1-v1_5 signature verification against an RSA JWK."""
    if jwk.get("kty") not in (None, "RSA"):
        return False
    n_b64 = jwk.get("n")
    e_b64 = jwk.get("e")
    if not isinstance(n_b64, str) or not isinstance(e_b64, str):
        return False

    try:
        n = int.from_bytes(_b64url_decode(n_b64), "big")
        e = int.from_bytes(_b64url_decode(e_b64), "big")
    except Exception:
        return False

    if n.bit_length() < 1024 or e < 3 or (e % 2 == 0):
        return False

    modulus_len = (n.bit_length() + 7) // 8
    if len(signature) != modulus_len:
        return False

    sig_int = int.from_bytes(signature, "big")
    if sig_int >= n:
        return False

    em = pow(sig_int, e, n).to_bytes(modulus_len, "big")
    hash_fn, der_prefix = _RSA_DIGEST_INFO_PREFIXES[alg]
    digest = hash_fn(signing_input).digest()
    digest_info = der_prefix + digest
    ps_len = modulus_len - len(digest_info) - 3
    if ps_len < 8:
        return False

    expected_em = b"\x00\x01" + (b"\xff" * ps_len) + b"\x00" + digest_info
    return hmac.compare_digest(em, expected_em)


def _verify_ec_signature(
    alg: str,
    signing_input: bytes,
    signature: bytes,
    jwk: dict[str, Any],
) -> bool:
    """Verify ES256/ES384/ES512 JWT signatures when optional `cryptography` is installed."""
    if jwk.get("kty") != "EC":
        return False
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    except ImportError as exc:
        raise OIDCVerificationError(
            f"OIDC algorithm '{alg}' requires the optional 'cryptography' package."
        ) from exc

    curve_map: dict[str, tuple[ec.EllipticCurve, hashes.HashAlgorithm, int]] = {
        "ES256": (ec.SECP256R1(), hashes.SHA256(), 32),
        "ES384": (ec.SECP384R1(), hashes.SHA384(), 48),
        "ES512": (ec.SECP521R1(), hashes.SHA512(), 66),
    }
    curve, hash_alg, coord_size = curve_map[alg]
    if len(signature) != coord_size * 2:
        return False

    x_b64 = jwk.get("x")
    y_b64 = jwk.get("y")
    if not isinstance(x_b64, str) or not isinstance(y_b64, str):
        return False

    x = int.from_bytes(_b64url_decode(x_b64), "big")
    y = int.from_bytes(_b64url_decode(y_b64), "big")
    public_key = ec.EllipticCurvePublicNumbers(x, y, curve).public_key()
    r = int.from_bytes(signature[:coord_size], "big")
    s = int.from_bytes(signature[coord_size:], "big")
    der_sig = encode_dss_signature(r, s)
    try:
        public_key.verify(der_sig, signing_input, ec.ECDSA(hash_alg))
        return True
    except Exception:
        return False


class JWKSCache:
    """Per-issuer TTL cache for OpenID discovery metadata and JWKS keys."""

    def __init__(self, ttl_seconds: int = 300) -> None:
        self._ttl_seconds = ttl_seconds
        self._discovery: dict[str, tuple[float, dict[str, Any]]] = {}
        self._jwks: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    def clear(self) -> None:
        self._discovery.clear()
        self._jwks.clear()

    async def get_discovery(
        self,
        issuer_url: str,
        *,
        force_refresh: bool = False,
        http_client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any]:
        normalized = issuer_url.strip().rstrip("/")
        now = time.monotonic()
        cached = self._discovery.get(normalized)
        if cached and not force_refresh and (now - cached[0]) < self._ttl_seconds:
            return cached[1]

        discovery_url = f"{normalized}/.well-known/openid-configuration"
        close_client = http_client is None
        client = http_client or httpx.AsyncClient(timeout=10.0)
        try:
            resp = await client.get(discovery_url)
            resp.raise_for_status()
            doc = resp.json()
            if not isinstance(doc, dict):
                raise OIDCVerificationError("OIDC discovery document must be a JSON object.")
            self._discovery[normalized] = (now, doc)
            return doc
        except OIDCVerificationError:
            raise
        except Exception as exc:
            raise OIDCVerificationError(f"Failed to fetch OIDC discovery document: {exc}") from exc
        finally:
            if close_client:
                await client.aclose()

    async def get_jwks(
        self,
        issuer_url: str,
        *,
        force_refresh: bool = False,
        http_client: httpx.AsyncClient | None = None,
    ) -> list[dict[str, Any]]:
        normalized = issuer_url.strip().rstrip("/")
        now = time.monotonic()
        cached = self._jwks.get(normalized)
        if cached and not force_refresh and (now - cached[0]) < self._ttl_seconds:
            return cached[1]

        discovery = await self.get_discovery(
            normalized, force_refresh=force_refresh, http_client=http_client
        )
        jwks_uri = str(discovery.get("jwks_uri") or "").strip()
        if not jwks_uri:
            raise OIDCVerificationError("OIDC discovery metadata missing jwks_uri.")

        close_client = http_client is None
        client = http_client or httpx.AsyncClient(timeout=10.0)
        try:
            resp = await client.get(jwks_uri)
            resp.raise_for_status()
            payload = resp.json()
            keys = payload.get("keys") if isinstance(payload, dict) else None
            if not isinstance(keys, list) or not keys:
                raise OIDCVerificationError("OIDC JWKS endpoint returned no signing keys.")
            valid_keys = [k for k in keys if isinstance(k, dict)]
            self._jwks[normalized] = (now, valid_keys)
            return valid_keys
        except OIDCVerificationError:
            raise
        except Exception as exc:
            raise OIDCVerificationError(f"Failed to fetch OIDC JWKS keys: {exc}") from exc
        finally:
            if close_client:
                await client.aclose()


default_jwks_cache = JWKSCache()


def clear_oidc_jwks_cache() -> None:
    """Clear module-level JWKS and discovery caches (primarily for testing)."""
    default_jwks_cache.clear()


def _select_matching_keys(keys: list[dict[str, Any]], kid: str | None) -> list[dict[str, Any]]:
    if kid:
        return [k for k in keys if k.get("kid") == kid]
    return [k for k in keys if k.get("use") in (None, "sig")]


def _verify_jwk_signature(
    alg: str,
    signing_input: bytes,
    signature: bytes,
    jwk: dict[str, Any],
) -> bool:
    if alg in _SUPPORTED_RSA_ALGS:
        return _verify_rsa_pkcs1_v15(alg, signing_input, signature, jwk)
    if alg in _SUPPORTED_EC_ALGS:
        return _verify_ec_signature(alg, signing_input, signature, jwk)
    return False


async def verify_oidc_id_token(
    id_token: str,
    settings: Settings,
    *,
    nonce: str | None = None,
    jwks_cache: JWKSCache | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Verify OIDC ID token signature via issuer JWKS and validate `iss`, `aud`, `exp`, `nbf`."""
    parts = id_token.strip().split(".")
    if len(parts) != 3:
        raise OIDCVerificationError("Malformed OIDC ID token (expected 3 JWT segments).")
    header_b64, payload_b64, sig_b64 = parts

    try:
        header = json.loads(_b64url_decode(header_b64).decode("utf-8"))
        claims = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
        signature = _b64url_decode(sig_b64)
    except Exception as exc:
        raise OIDCVerificationError(f"Invalid OIDC JWT encoding: {exc}") from exc

    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise OIDCVerificationError("Malformed OIDC JWT header or payload object.")

    alg = str(header.get("alg") or "")
    if alg not in _SUPPORTED_RSA_ALGS and alg not in _SUPPORTED_EC_ALGS:
        raise OIDCVerificationError(f"Unsupported or disallowed OIDC JWT algorithm '{alg}'.")

    kid_raw = header.get("kid")
    kid = str(kid_raw) if kid_raw is not None else None
    cache = jwks_cache or default_jwks_cache

    keys = await cache.get_jwks(
        settings.oidc_issuer_url, force_refresh=False, http_client=http_client
    )
    candidates = _select_matching_keys(keys, kid)
    if not candidates and kid:
        keys = await cache.get_jwks(
            settings.oidc_issuer_url, force_refresh=True, http_client=http_client
        )
        candidates = _select_matching_keys(keys, kid)

    if not candidates:
        raise OIDCVerificationError(f"No matching signing key found in OIDC JWKS for kid='{kid}'.")

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    if not any(_verify_jwk_signature(alg, signing_input, signature, k) for k in candidates):
        raise OIDCVerificationError("OIDC ID token signature verification failed.")

    expected_issuer = settings.oidc_issuer_url.strip().rstrip("/")
    actual_issuer = str(claims.get("iss") or "").strip().rstrip("/")
    if not actual_issuer or actual_issuer != expected_issuer:
        raise OIDCVerificationError(
            f"OIDC issuer mismatch: expected '{expected_issuer}', got '{actual_issuer}'."
        )

    expected_aud = (settings.oidc_audience or settings.oidc_client_id).strip()
    if expected_aud:
        raw_aud = claims.get("aud")
        aud_values = (
            [str(a) for a in raw_aud]
            if isinstance(raw_aud, list)
            else ([str(raw_aud)] if raw_aud is not None else [])
        )
        if expected_aud not in aud_values:
            raise OIDCVerificationError(
                f"OIDC audience mismatch: expected '{expected_aud}' in token aud."
            )

    now_ts = int(datetime.now(UTC).timestamp())
    exp_raw = claims.get("exp")
    if not isinstance(exp_raw, (int, float)):
        raise OIDCVerificationError("OIDC ID token is missing required 'exp' claim.")
    if now_ts - _CLOCK_LEEWAY_SECONDS > int(exp_raw):
        raise OIDCVerificationError("OIDC ID token has expired.")

    nbf_raw = claims.get("nbf")
    if nbf_raw is not None:
        if not isinstance(nbf_raw, (int, float)):
            raise OIDCVerificationError("OIDC ID token contains invalid 'nbf' claim.")
        if now_ts + _CLOCK_LEEWAY_SECONDS < int(nbf_raw):
            raise OIDCVerificationError("OIDC ID token is not yet valid ('nbf').")

    if nonce is not None and claims.get("nonce") != nonce:
        raise OIDCVerificationError("OIDC ID token nonce mismatch.")

    return claims
