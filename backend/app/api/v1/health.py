from fastapi import APIRouter, Depends
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
