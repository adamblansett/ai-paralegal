import time
import uuid

import structlog
from opentelemetry.trace import SpanKind
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings
from app.core.metrics import metrics_registry, normalize_metric_path
from app.core.telemetry import current_trace_id_hex, extract_trace_context, get_tracer

_http_tracer = get_tracer("ai_paralegal.http")


class SecurityAndObservabilityMiddleware:
    """Pure ASGI middleware for X-Request-ID, OpenTelemetry spans, security headers & metrics."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "GET"))
        path = str(scope.get("path", "/"))
        norm_path = normalize_metric_path(path)
        raw_headers = scope.get("headers") or []

        header_carrier: dict[str, str] = {}
        request_id: str | None = None
        for k, v in raw_headers:
            k_str = k.decode("latin-1").lower()
            v_str = v.decode("latin-1", errors="ignore")
            header_carrier[k_str] = v_str
            if k_str == "x-request-id" and v_str.strip() and request_id is None:
                request_id = v_str.strip()[:128]
        if not request_id:
            request_id = str(uuid.uuid4())

        parent_ctx = extract_trace_context(header_carrier)
        t0 = time.perf_counter()
        status_code = 500
        trace_id_hex: str | None = None

        with _http_tracer.start_as_current_span(
            f"HTTP {method} {norm_path}",
            context=parent_ctx,
            kind=SpanKind.SERVER,
        ) as span:
            span.set_attribute("http.method", method)
            span.set_attribute("http.route", norm_path)
            span.set_attribute("http.request_id", request_id)
            trace_id_hex = current_trace_id_hex()

            ctx_vars: dict[str, str] = {"request_id": request_id}
            if trace_id_hex:
                ctx_vars["trace_id"] = trace_id_hex
            structlog.contextvars.bind_contextvars(**ctx_vars)

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
                    if trace_id_hex:
                        _set_header(b"x-trace-id", trace_id_hex.encode("latin-1"))
                    _set_header(b"x-content-type-options", b"nosniff")
                    _set_header(b"x-frame-options", b"DENY")
                    _set_header(b"referrer-policy", b"strict-origin-when-cross-origin")
                    _set_header(
                        b"permissions-policy", b"camera=(), microphone=(), geolocation=()"
                    )
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
                span.set_attribute("http.status_code", status_code)
                duration = time.perf_counter() - t0
                metrics_registry.record_http_request(method, path, status_code, duration)
                unbind_keys = ["request_id", "trace_id"] if trace_id_hex else ["request_id"]
                structlog.contextvars.unbind_contextvars(*unbind_keys)
