import time
import uuid

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings
from app.core.metrics import metrics_registry


class SecurityAndObservabilityMiddleware:
    """Pure ASGI middleware for X-Request-ID tracing, security headers, and Prometheus metrics."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "GET"))
        path = str(scope.get("path", "/"))
        raw_headers = scope.get("headers") or []

        request_id: str | None = None
        for k, v in raw_headers:
            if k.lower() == b"x-request-id":
                candidate = v.decode("latin-1", errors="ignore").strip()
                if candidate:
                    request_id = candidate[:128]
                break
        if not request_id:
            request_id = str(uuid.uuid4())

        structlog.contextvars.bind_contextvars(request_id=request_id)
        t0 = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status", 200))
                headers = list(message.get("headers") or [])
                existing = {k.lower() for k, _ in headers}

                def _set_header(name: bytes, value: bytes) -> None:
                    if name.lower() not in existing:
                        headers.append((name, value))
                        existing.add(name.lower())

                _set_header(b"x-request-id", request_id.encode("latin-1"))
                _set_header(b"x-content-type-options", b"nosniff")
                _set_header(b"x-frame-options", b"DENY")
                _set_header(b"referrer-policy", b"strict-origin-when-cross-origin")
                _set_header(b"permissions-policy", b"camera=(), microphone=(), geolocation=()")
                if self.settings.app_env == "production":
                    _set_header(
                        b"strict-transport-security",
                        b"max-age=31536000; includeSubDomains",
                    )
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.perf_counter() - t0
            metrics_registry.record_http_request(method, path, status_code, duration)
            structlog.contextvars.unbind_contextvars("request_id")
