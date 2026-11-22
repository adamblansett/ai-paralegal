from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_v1_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import dispose_engine


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.storage_backend == "local":
        Path(settings.local_storage_dir).mkdir(parents=True, exist_ok=True)
    yield
    await dispose_engine()


def create_app(settings_override: Settings | None = None) -> FastAPI:
    """Construct and configure the AI Paralegal FastAPI application."""
    cfg = settings_override or get_settings()

    app = FastAPI(
        title=cfg.app_name,
        version="0.1.0",
        description=(
            "AI Paralegal platform for legal document ingestion, contract & clause analysis, "
            "discovery synthesis, citation-grounded drafting, and human attorney review."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_v1_router, prefix=cfg.api_v1_prefix)
    return app


app = create_app()
