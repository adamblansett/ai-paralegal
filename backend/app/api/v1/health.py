from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_app_settings, get_db, get_router
from app.core.config import Settings
from app.core.disclaimer import LEGAL_DISCLAIMER
from app.llm.router import ModelRouter
from app.schemas.common import HealthResponse
from app.services.ocr import OCRService

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check(
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    model_router: ModelRouter = Depends(get_router),
) -> HealthResponse:
    """Verify API readiness, database connectivity, and active LLM routing config."""
    db_status = "ok"
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        db_status = f"error: {exc}"

    return HealthResponse(
        status="ok" if db_status == "ok" else "degraded",
        database=db_status,
        app_name=settings.app_name,
        environment=settings.app_env,
        default_provider=settings.llm_default_provider,
        fallback_providers=settings.fallback_provider_list,
        supported_models=model_router.supported_models(),
        storage_backend=settings.storage_backend,
        task_execution_mode=settings.task_execution_mode,
        auth_enabled=True,
        ocr_backend=settings.ocr_backend,
        tesseract_available=OCRService.is_tesseract_available(),
        max_upload_size_mb=settings.max_upload_size_mb,
        pii_cloud_redaction_enabled=settings.llm_redact_pii_for_cloud,
        oidc_enabled=settings.oidc_enabled,
        disclaimer=LEGAL_DISCLAIMER,
    )


@router.get("/health/live")
async def liveness_probe() -> dict[str, str]:
    """Fast Kubernetes liveness probe confirming the Python process is responsive."""
    return {"status": "alive"}


@router.get("/health/ready")
async def readiness_probe(
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    """Strict Kubernetes readiness probe checking database and Redis (when Celery active)."""
    checks: dict[str, str] = {"database": "ok", "redis": "not_required"}
    ready = True

    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        checks["database"] = f"error: {exc}"
        ready = False

    requires_redis = (
        settings.task_execution_mode == "celery"
        or settings.rate_limit_backend == "redis"
        or settings.event_bus_backend == "redis"
    )
    if requires_redis and settings.redis_url:
        client = aioredis.from_url(
            settings.redis_url, socket_connect_timeout=1.5, socket_timeout=1.5
        )
        try:
            await client.ping()
            checks["redis"] = "ok"
        except Exception as exc:
            checks["redis"] = f"error: {exc}"
            ready = False
        finally:
            await client.aclose()

    if not ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "not_ready", "checks": checks},
        )
    return {"status": "ready", "checks": checks}
