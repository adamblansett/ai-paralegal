import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "password_hash",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "authorization",
        "api_key",
        "secret",
        "auth_secret_key",
        "signing_secret",
        "gemini_api_key",
        "s3_secret_access_key",
    }
)
_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9\-_=]+\.[A-Za-z0-9\-_=]+\.[A-Za-z0-9\-_=]+")
_QUERY_TOKEN_RE = re.compile(r"(?i)([?&]token=)[^&\s]+")


def _scrub_value(val: Any) -> Any:
    if isinstance(val, str):
        scrubbed = _BEARER_RE.sub("Bearer [REDACTED]", val)
        return _QUERY_TOKEN_RE.sub(r"\1[REDACTED]", scrubbed)
    if isinstance(val, dict):
        return _scrub_dict(val)
    if isinstance(val, list):
        return [_scrub_value(item) for item in val]
    return val


def _scrub_dict(mapping: MutableMapping[str, Any] | dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in mapping.items():
        if str(k).lower() in _SENSITIVE_KEYS:
            out[str(k)] = "[REDACTED]"
        else:
            out[str(k)] = _scrub_value(v)
    return out


def redact_sensitive_log_fields(
    _logger: Any,
    _method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Structlog processor redacting passwords, bearer JWTs, API keys, and webhook secrets."""
    return _scrub_dict(event_dict)


def configure_logging(log_level: str = "INFO") -> None:
    """Configure structured JSON logging with secret redaction for API and workers."""
    level = getattr(logging, log_level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_sensitive_log_fields,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
