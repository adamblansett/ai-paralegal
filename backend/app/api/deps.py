from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_db as _get_db
from app.llm.embeddings import EmbeddingService
from app.llm.router import ModelRouter
from app.storage import StorageBackend, get_storage_backend


async def get_db() -> AsyncIterator[AsyncSession]:
    async for session in _get_db():
        yield session


def get_app_settings() -> Settings:
    return get_settings()


def get_storage() -> StorageBackend:
    return get_storage_backend(get_settings())


def get_router() -> ModelRouter:
    return ModelRouter(get_settings())


def get_embeddings() -> EmbeddingService:
    return EmbeddingService(get_settings())
