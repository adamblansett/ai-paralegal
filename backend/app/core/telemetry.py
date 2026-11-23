from contextlib import suppress
from typing import Any

import structlog
from opentelemetry import trace
from opentelemetry.propagate import extract, inject
from opentelemetry.trace import Tracer

from app.core.config import Settings

logger = structlog.get_logger(__name__)
_TELEMETRY_CONFIGURED = False


def get_tracer(name: str = "ai_paralegal") -> Tracer:
    """Return an OpenTelemetry tracer (defaults to no-op tracer when SDK is not configured)."""
    return trace.get_tracer(name)


def extract_trace_context(headers: dict[str, str]) -> Any:
    """Extract W3C `traceparent` / `tracestate` context from incoming HTTP or task headers."""
    return extract(headers)


def inject_trace_headers(headers: dict[str, str] | None = None) -> dict[str, str]:
    """Inject active W3C trace context into outgoing HTTP or worker carrier headers."""
    carrier: dict[str, str] = dict(headers or {})
    inject(carrier)
    return carrier


def current_trace_id_hex() -> str | None:
    """Return the active 32-char hex OpenTelemetry trace_id if recording, else None."""
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx is not None and ctx.is_valid and ctx.trace_id:
        return f"{ctx.trace_id:032x}"
    return None


def configure_telemetry(settings: Settings, app: Any = None) -> bool:
    """Configure OpenTelemetry TracerProvider & optional FastAPI/SQLAlchemy/Celery hooks.

    Returns ``False`` cleanly when disabled or when optional SDK wheels are absent.
    """
    global _TELEMETRY_CONFIGURED
    if not settings.otel_enabled:
        return False

    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
        from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
    except ImportError as exc:
        logger.warning("opentelemetry_sdk_not_installed", reason=str(exc))
        return False

    if not _TELEMETRY_CONFIGURED:
        resource = Resource.create({"service.name": settings.otel_service_name})
        provider = TracerProvider(
            resource=resource,
            sampler=TraceIdRatioBased(settings.otel_sample_ratio),
        )
        if settings.otel_exporter_otlp_endpoint.strip():
            with suppress(ImportError):
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter,
                )

                exporter = OTLPSpanExporter(
                    endpoint=settings.otel_exporter_otlp_endpoint.strip()
                )
                provider.add_span_processor(BatchSpanProcessor(exporter))
        else:
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

        trace.set_tracer_provider(provider)
        _TELEMETRY_CONFIGURED = True

    if app is not None:
        with suppress(ImportError, Exception):
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            FastAPIInstrumentor.instrument_app(app)

    with suppress(ImportError, Exception):
        from opentelemetry.instrumentation.celery import CeleryInstrumentor

        CeleryInstrumentor().instrument()

    return True
