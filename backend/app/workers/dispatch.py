import uuid

import structlog
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.session import get_session_factory
from app.llm.embeddings import EmbeddingService
from app.llm.router import ModelRouter
from app.models.review_task import ReviewTask
from app.services.contract_review import run_batch_contract_review, run_contract_review
from app.services.discovery import run_discovery_synthesis
from app.services.drafting import run_draft_generation
from app.services.ingestion import process_document
from app.storage.base import StorageBackend
from app.workers.tasks import (
    batch_contract_review_task,
    contract_review_task,
    discovery_synthesis_task,
    draft_generation_task,
    ingest_document_task,
)

logger = structlog.get_logger(__name__)


async def _bg_ingest(
    document_id: uuid.UUID,
    review_task_id: uuid.UUID | None,
    storage: StorageBackend,
    embeddings: EmbeddingService,
) -> None:
    factory = get_session_factory()
    async with factory() as session:
        try:
            await process_document(
                session,
                storage,
                embeddings,
                document_id=document_id,
                review_task_id=review_task_id,
            )
        except Exception as exc:
            logger.error(
                "background_ingestion_failed", document_id=str(document_id), error=str(exc)
            )


async def _bg_contract_review(
    matter_id: uuid.UUID,
    task_id: uuid.UUID,
    document_ids: list[uuid.UUID] | None,
    clause_types: list[str] | None,
    playbook_notes: str | None,
    playbook_id: uuid.UUID | None,
    provider: str | None,
    model: str | None,
    router: ModelRouter,
    settings: Settings,
) -> None:
    factory = get_session_factory(settings)
    async with factory() as session:
        try:
            await run_contract_review(
                session,
                router,
                matter_id=matter_id,
                task_id=task_id,
                document_ids=document_ids,
                clause_types=clause_types,
                playbook_notes=playbook_notes,
                playbook_id=playbook_id,
                provider=provider,
                model=model,
                settings=settings,
            )
        except Exception as exc:
            logger.error("background_contract_review_failed", task_id=str(task_id), error=str(exc))


async def _bg_discovery_synthesis(
    matter_id: uuid.UUID,
    task_id: uuid.UUID,
    document_ids: list[uuid.UUID] | None,
    focus_topics: list[str] | None,
    provider: str | None,
    model: str | None,
    router: ModelRouter,
    settings: Settings,
) -> None:
    factory = get_session_factory(settings)
    async with factory() as session:
        try:
            await run_discovery_synthesis(
                session,
                router,
                matter_id=matter_id,
                task_id=task_id,
                document_ids=document_ids,
                focus_topics=focus_topics,
                provider=provider,
                model=model,
                settings=settings,
            )
        except Exception as exc:
            logger.error("background_discovery_failed", task_id=str(task_id), error=str(exc))


async def _bg_draft_generation(
    matter_id: uuid.UUID,
    task_id: uuid.UUID,
    draft_type: str,
    instructions: str,
    document_ids: list[uuid.UUID] | None,
    provider: str | None,
    model: str | None,
    router: ModelRouter,
    embeddings: EmbeddingService,
    settings: Settings,
) -> None:
    factory = get_session_factory(settings)
    async with factory() as session:
        try:
            await run_draft_generation(
                session,
                router,
                embeddings,
                matter_id=matter_id,
                task_id=task_id,
                draft_type=draft_type,
                instructions=instructions,
                document_ids=document_ids,
                provider=provider,
                model=model,
                settings=settings,
            )
        except Exception as exc:
            logger.error("background_drafting_failed", task_id=str(task_id), error=str(exc))


async def dispatch_ingestion(
    *,
    session: AsyncSession,
    settings: Settings,
    background_tasks: BackgroundTasks,
    storage: StorageBackend,
    embeddings: EmbeddingService,
    document_id: uuid.UUID,
    task: ReviewTask,
) -> None:
    """Dispatch document ingestion via Celery, FastAPI BackgroundTasks, or inline await."""
    if settings.task_execution_mode == "celery":
        async_result = ingest_document_task.delay(str(document_id), str(task.id))
        task.celery_task_id = str(async_result.id)
        await session.commit()
    elif settings.task_execution_mode == "background":
        background_tasks.add_task(_bg_ingest, document_id, task.id, storage, embeddings)
    else:
        try:
            await process_document(
                session,
                storage,
                embeddings,
                document_id=document_id,
                review_task_id=task.id,
            )
        except Exception as exc:
            logger.error("inline_ingestion_failed", document_id=str(document_id), error=str(exc))


async def dispatch_contract_review(
    *,
    session: AsyncSession,
    settings: Settings,
    background_tasks: BackgroundTasks,
    router: ModelRouter,
    matter_id: uuid.UUID,
    task: ReviewTask,
    document_ids: list[uuid.UUID] | None,
    clause_types: list[str] | None,
    playbook_notes: str | None,
    playbook_id: uuid.UUID | None = None,
    provider: str | None,
    model: str | None,
) -> None:
    """Dispatch contract review via Celery, FastAPI BackgroundTasks, or inline await."""
    if settings.task_execution_mode == "celery":
        async_result = contract_review_task.delay(
            str(matter_id),
            str(task.id),
            [str(d) for d in document_ids] if document_ids else None,
            clause_types,
            playbook_notes,
            str(playbook_id) if playbook_id else None,
            provider,
            model,
        )
        task.celery_task_id = str(async_result.id)
        await session.commit()
    elif settings.task_execution_mode == "background":
        background_tasks.add_task(
            _bg_contract_review,
            matter_id,
            task.id,
            document_ids,
            clause_types,
            playbook_notes,
            playbook_id,
            provider,
            model,
            router,
            settings,
        )
    else:
        try:
            await run_contract_review(
                session,
                router,
                matter_id=matter_id,
                task_id=task.id,
                document_ids=document_ids,
                clause_types=clause_types,
                playbook_notes=playbook_notes,
                playbook_id=playbook_id,
                provider=provider,
                model=model,
                settings=settings,
            )
        except Exception as exc:
            logger.error("inline_contract_review_failed", task_id=str(task.id), error=str(exc))


async def _bg_batch_contract_review(
    matter_id: uuid.UUID,
    task_id: uuid.UUID,
    document_ids: list[uuid.UUID] | None,
    clause_types: list[str] | None,
    playbook_notes: str | None,
    playbook_id: uuid.UUID | None,
    provider: str | None,
    model: str | None,
    router: ModelRouter,
    settings: Settings,
) -> None:
    factory = get_session_factory(settings)
    async with factory() as session:
        try:
            await run_batch_contract_review(
                session,
                router,
                matter_id=matter_id,
                task_id=task_id,
                document_ids=document_ids,
                clause_types=clause_types,
                playbook_notes=playbook_notes,
                playbook_id=playbook_id,
                provider=provider,
                model=model,
                settings=settings,
            )
        except Exception as exc:
            logger.error("background_batch_review_failed", task_id=str(task_id), error=str(exc))


async def dispatch_batch_contract_review(
    *,
    session: AsyncSession,
    settings: Settings,
    background_tasks: BackgroundTasks,
    router: ModelRouter,
    matter_id: uuid.UUID,
    task: ReviewTask,
    document_ids: list[uuid.UUID] | None,
    clause_types: list[str] | None,
    playbook_notes: str | None,
    playbook_id: uuid.UUID | None = None,
    provider: str | None,
    model: str | None,
) -> None:
    """Dispatch batch per-document contract review via Celery, BackgroundTasks, or inline."""
    if settings.task_execution_mode == "celery":
        async_result = batch_contract_review_task.delay(
            str(matter_id),
            str(task.id),
            [str(d) for d in document_ids] if document_ids else None,
            clause_types,
            playbook_notes,
            str(playbook_id) if playbook_id else None,
            provider,
            model,
        )
        task.celery_task_id = str(async_result.id)
        await session.commit()
    elif settings.task_execution_mode == "background":
        background_tasks.add_task(
            _bg_batch_contract_review,
            matter_id,
            task.id,
            document_ids,
            clause_types,
            playbook_notes,
            playbook_id,
            provider,
            model,
            router,
            settings,
        )
    else:
        try:
            await run_batch_contract_review(
                session,
                router,
                matter_id=matter_id,
                task_id=task.id,
                document_ids=document_ids,
                clause_types=clause_types,
                playbook_notes=playbook_notes,
                playbook_id=playbook_id,
                provider=provider,
                model=model,
                settings=settings,
            )
        except Exception as exc:
            logger.error("inline_batch_review_failed", task_id=str(task.id), error=str(exc))


async def dispatch_discovery_synthesis(
    *,
    session: AsyncSession,
    settings: Settings,
    background_tasks: BackgroundTasks,
    router: ModelRouter,
    matter_id: uuid.UUID,
    task: ReviewTask,
    document_ids: list[uuid.UUID] | None,
    focus_topics: list[str] | None,
    provider: str | None,
    model: str | None,
) -> None:
    """Dispatch discovery synthesis via Celery, FastAPI BackgroundTasks, or inline await."""
    if settings.task_execution_mode == "celery":
        async_result = discovery_synthesis_task.delay(
            str(matter_id),
            str(task.id),
            [str(d) for d in document_ids] if document_ids else None,
            focus_topics,
            provider,
            model,
        )
        task.celery_task_id = str(async_result.id)
        await session.commit()
    elif settings.task_execution_mode == "background":
        background_tasks.add_task(
            _bg_discovery_synthesis,
            matter_id,
            task.id,
            document_ids,
            focus_topics,
            provider,
            model,
            router,
            settings,
        )
    else:
        try:
            await run_discovery_synthesis(
                session,
                router,
                matter_id=matter_id,
                task_id=task.id,
                document_ids=document_ids,
                focus_topics=focus_topics,
                provider=provider,
                model=model,
                settings=settings,
            )
        except Exception as exc:
            logger.error("inline_discovery_failed", task_id=str(task.id), error=str(exc))


async def dispatch_draft_generation(
    *,
    session: AsyncSession,
    settings: Settings,
    background_tasks: BackgroundTasks,
    router: ModelRouter,
    embeddings: EmbeddingService,
    matter_id: uuid.UUID,
    task: ReviewTask,
    draft_type: str,
    instructions: str,
    document_ids: list[uuid.UUID] | None,
    provider: str | None,
    model: str | None,
) -> None:
    """Dispatch legal drafting via Celery, FastAPI BackgroundTasks, or inline await."""
    if settings.task_execution_mode == "celery":
        async_result = draft_generation_task.delay(
            str(matter_id),
            str(task.id),
            draft_type,
            instructions,
            [str(d) for d in document_ids] if document_ids else None,
            provider,
            model,
        )
        task.celery_task_id = str(async_result.id)
        await session.commit()
    elif settings.task_execution_mode == "background":
        background_tasks.add_task(
            _bg_draft_generation,
            matter_id,
            task.id,
            draft_type,
            instructions,
            document_ids,
            provider,
            model,
            router,
            embeddings,
            settings,
        )
    else:
        try:
            await run_draft_generation(
                session,
                router,
                embeddings,
                matter_id=matter_id,
                task_id=task.id,
                draft_type=draft_type,
                instructions=instructions,
                document_ids=document_ids,
                provider=provider,
                model=model,
                settings=settings,
            )
        except Exception as exc:
            logger.error("inline_drafting_failed", task_id=str(task.id), error=str(exc))
