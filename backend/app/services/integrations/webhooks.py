import asyncio
import hashlib
import hmac
import ipaddress
import json
import socket
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.organization import WebhookSubscription

logger = structlog.get_logger(__name__)


def _is_blocked_ip(ip_str: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def validate_webhook_url(url: str, *, allow_private_ips: bool = False) -> str:
    """Validate webhook URL scheme and guard against SSRF toward private/metadata IPs."""
    cleaned = url.strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        raise ValueError("Webhook target_url must be a valid http(s) URL with hostname.")

    if allow_private_ips:
        return cleaned

    host = parsed.hostname.strip().lower()
    if host in {"localhost", "metadata.google.internal"}:
        raise ValueError(f"Webhook host '{host}' is blocked by SSRF policy.")

    try:
        ip_literal = ipaddress.ip_address(host)
        if _is_blocked_ip(str(ip_literal)):
            raise ValueError(f"Webhook IP '{host}' is blocked by SSRF policy.")
        return cleaned
    except ValueError as exc:
        if "SSRF policy" in str(exc):
            raise

    try:
        infos = socket.getaddrinfo(host, None)
        for info in infos:
            resolved_ip = str(info[4][0])
            if _is_blocked_ip(resolved_ip):
                raise ValueError(
                    f"Webhook hostname '{host}' resolves to blocked internal IP {resolved_ip}."
                )
    except socket.gaierror:
        # Defer DNS failures in offline environments unless IP literally resolved private
        pass

    return cleaned


def sign_webhook_payload(secret: str, body_bytes: bytes) -> str:
    """Compute HMAC-SHA256 webhook signature header."""
    digest = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def format_webhook_body(
    channel_type: str,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Format event payload for Slack Block Kit, MS Teams MessageCard, or generic webhooks."""
    summary = str(payload.get("summary") or f"AI Paralegal event: {event_type}")
    matter_title = str(payload.get("matter_title") or payload.get("matter_id") or "Matter")
    if channel_type == "slack":
        return {
            "text": f"[{event_type}] {matter_title}: {summary}",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*AI Paralegal Alert (`{event_type}`)*\n"
                            f"*Matter:* {matter_title}\n{summary}"
                        ),
                    },
                }
            ],
            "metadata": payload,
        }
    if channel_type == "teams":
        return {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "themeColor": "C2410C" if "escalat" in event_type or "hold" in event_type else "4F46E5",
            "summary": f"{event_type}: {matter_title}",
            "title": f"AI Paralegal: {event_type}",
            "text": f"Matter: {matter_title} — {summary}",
        }
    return {
        "event_type": event_type,
        "timestamp": datetime.now(UTC).isoformat(),
        "payload": payload,
    }


async def send_single_webhook(
    sub: WebhookSubscription,
    *,
    event_type: str,
    payload: dict[str, Any],
    settings: Settings | None = None,
) -> tuple[bool, int | None, str | None]:
    """Deliver a single signed webhook with timeout and capped exponential backoff."""
    cfg = settings or get_settings()
    try:
        validate_webhook_url(sub.target_url, allow_private_ips=cfg.webhook_allow_private_ips)
    except ValueError as exc:
        return False, None, str(exc)

    body_dict = format_webhook_body(sub.channel_type, event_type, payload)
    body_bytes = json.dumps(body_dict, separators=(",", ":"), default=str).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-AI-Paralegal-Event": event_type,
        "X-AI-Paralegal-Delivery": str(uuid.uuid4()),
    }
    if sub.signing_secret:
        headers["X-AI-Paralegal-Signature"] = sign_webhook_payload(sub.signing_secret, body_bytes)

    last_error: str | None = None
    last_status: int | None = None
    max_attempts = 3

    async with httpx.AsyncClient(timeout=5.0) as client:
        for attempt in range(1, max_attempts + 1):
            try:
                resp = await client.post(sub.target_url, content=body_bytes, headers=headers)
                last_status = resp.status_code
                if 200 <= resp.status_code < 300:
                    return True, resp.status_code, None
                last_error = f"HTTP {resp.status_code}"
                if resp.status_code < 500:
                    break
            except httpx.HTTPError as exc:
                last_error = str(exc)

            if attempt < max_attempts:
                await asyncio.sleep(0.15 * (2 ** (attempt - 1)))

    return False, last_status, last_error


async def dispatch_webhooks(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID | None,
    matter_id: uuid.UUID | None,
    event_type: str,
    payload: dict[str, Any],
    settings: Settings | None = None,
) -> int:
    """Deliver event notifications to all matching active webhook subscriptions."""
    stmt = select(WebhookSubscription).where(WebhookSubscription.is_active.is_(True))
    subs = list((await session.execute(stmt)).scalars().all())
    matched: list[WebhookSubscription] = []
    for sub in subs:
        if sub.organization_id and organization_id and sub.organization_id != organization_id:
            continue
        if sub.matter_id and matter_id and sub.matter_id != matter_id:
            continue
        events = sub.events_json or []
        if events and event_type not in events and "*" not in events:
            continue
        matched.append(sub)

    delivered = 0
    for sub in matched:
        ok, status_code, err = await send_single_webhook(
            sub, event_type=event_type, payload=payload, settings=settings
        )
        if ok:
            delivered += 1
        else:
            logger.warning(
                "webhook_delivery_failed",
                subscription_id=str(sub.id),
                event_type=event_type,
                status_code=status_code,
                error=err,
            )
    return delivered
