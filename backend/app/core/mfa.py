import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote


def generate_totp_secret(num_bytes: int = 20) -> str:
    """Generate a random RFC 4648 Base32 secret suitable for authenticator TOTP apps."""
    return base64.b32encode(secrets.token_bytes(num_bytes)).decode("ascii").rstrip("=")


def _decode_base32_secret(secret: str) -> bytes:
    cleaned = secret.strip().upper().replace(" ", "").replace("-", "")
    padding = "=" * ((8 - len(cleaned) % 8) % 8)
    return base64.b32decode(cleaned + padding, casefold=True)


def generate_totp_code(
    secret: str,
    *,
    for_time: int | None = None,
    step_seconds: int = 30,
    digits: int = 6,
) -> str:
    """Compute an RFC 6238 / RFC 4226 HMAC-SHA1 TOTP verification code."""
    key = _decode_base32_secret(secret)
    timestamp = int(time.time()) if for_time is None else int(for_time)
    counter = timestamp // step_seconds
    counter_bytes = struct.pack(">Q", counter)
    digest = hmac.new(key, counter_bytes, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    otp = binary % (10**digits)
    return f"{otp:0{digits}d}"


def verify_totp_code(
    secret: str,
    code: str,
    *,
    for_time: int | None = None,
    step_seconds: int = 30,
    digits: int = 6,
    window: int = 1,
) -> bool:
    """Verify a user-supplied TOTP code in constant time across +/- `window` intervals."""
    cleaned_code = code.strip().replace(" ", "").replace("-", "")
    if len(cleaned_code) != digits or not cleaned_code.isdigit():
        return False

    base_ts = int(time.time()) if for_time is None else int(for_time)
    try:
        for delta in range(-window, window + 1):
            candidate = generate_totp_code(
                secret,
                for_time=base_ts + delta * step_seconds,
                step_seconds=step_seconds,
                digits=digits,
            )
            if hmac.compare_digest(candidate, cleaned_code):
                return True
    except Exception:
        return False
    return False


def build_otpauth_uri(*, secret: str, email: str, issuer: str = "AI Paralegal") -> str:
    """Build standard `otpauth://totp/...` provisioning URI for QR enrollment."""
    label = f"{quote(issuer)}:{quote(email)}"
    return (
        f"otpauth://totp/{label}?secret={secret}"
        f"&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"
    )
