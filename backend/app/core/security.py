import base64
import hashlib
import hmac
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from app.core.config import Settings

ALLOWED_ROLES: tuple[str, ...] = ("partner", "associate", "paralegal", "auditor")
ATTORNEY_ROLES: tuple[str, ...] = ("partner", "associate")
ACCESS_LEVELS: tuple[str, ...] = ("owner", "editor", "reviewer", "viewer")
_PBKDF2_ITERATIONS = 200_000


class TokenError(ValueError):
    """Raised when a bearer token is malformed, expired, or has an invalid signature."""


class TokenPayload(BaseModel):
    """Validated claims extracted from an HS256 JWT bearer token."""

    sub: str
    email: str
    full_name: str
    role: str
    exp: int
    jti: str | None = None


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * ((4 - len(segment) % 4) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def hash_password(password: str) -> str:
    """Hash a plaintext password using PBKDF2-HMAC-SHA256 with a random 16-byte salt."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _PBKDF2_ITERATIONS,
    )
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${_b64url_encode(salt)}${_b64url_encode(digest)}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Constant-time verification of a password against a stored PBKDF2-SHA256 hash."""
    try:
        scheme, iter_str, salt_b64, expected_b64 = stored_hash.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iter_str)
        salt = _b64url_decode(salt_b64)
        expected = _b64url_decode(expected_b64)
    except Exception:
        return False

    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(actual, expected)


def create_access_token(
    *,
    user_id: str,
    email: str,
    full_name: str,
    role: str,
    settings: Settings,
) -> str:
    """Create a signed HS256 JWT access token."""
    header = {"alg": "HS256", "typ": "JWT"}
    exp_dt = datetime.now(UTC) + timedelta(minutes=settings.auth_token_expire_minutes)
    payload = {
        "sub": user_id,
        "email": email,
        "full_name": full_name,
        "role": role,
        "exp": int(exp_dt.timestamp()),
        "jti": str(uuid.uuid4()),
    }
    header_b64 = _b64url_encode(
        json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    payload_b64 = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = hmac.new(
        settings.auth_secret_key.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).digest()
    return f"{header_b64}.{payload_b64}.{_b64url_encode(sig)}"


def decode_access_token(token: str, settings: Settings) -> TokenPayload:
    """Verify signature and expiration of an HS256 JWT and return its claims."""
    parts = token.strip().split(".")
    if len(parts) != 3:
        raise TokenError("Invalid bearer token format.")
    header_b64, payload_b64, sig_b64 = parts

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected_sig = hmac.new(
        settings.auth_secret_key.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).digest()
    try:
        actual_sig = _b64url_decode(sig_b64)
    except Exception as exc:
        raise TokenError("Malformed token signature.") from exc

    if not hmac.compare_digest(actual_sig, expected_sig):
        raise TokenError("Token signature verification failed.")

    try:
        header_data = json.loads(_b64url_decode(header_b64).decode("utf-8"))
        if header_data.get("alg") != "HS256":
            raise TokenError("Unsupported JWT algorithm.")
        payload_data = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
        claims = TokenPayload.model_validate(payload_data)
    except TokenError:
        raise
    except Exception as exc:
        raise TokenError("Malformed token payload.") from exc

    if claims.exp < int(datetime.now(UTC).timestamp()):
        raise TokenError("Access token has expired.")

    return claims


def hash_refresh_token(raw_token: str) -> str:
    """Compute SHA-256 hex digest of an opaque refresh token."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def generate_refresh_token() -> tuple[str, str]:
    """Generate a high-entropy opaque refresh token paired with its SHA-256 hash."""
    raw = secrets.token_urlsafe(48)
    return raw, hash_refresh_token(raw)
