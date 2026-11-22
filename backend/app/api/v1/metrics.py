from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from app.api.deps import get_current_user
from app.core.metrics import metrics_registry
from app.models.user import User

router = APIRouter(tags=["telemetry"])


async def prometheus_metrics_endpoint() -> PlainTextResponse:
    """Expose Prometheus text-format operational, OCR, PII, and LLM cost metrics."""
    return PlainTextResponse(
        content=metrics_registry.render_prometheus(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@router.get("/telemetry/summary")
async def get_telemetry_summary(
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Return aggregated request, LLM usage, estimated USD cost, and PII redaction metrics."""
    _ = current_user
    return metrics_registry.summary()
