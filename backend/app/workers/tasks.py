import asyncio
import uuid
from typing import Any

from app.core.config import get_settings
from app.db.session import build_worker_session_factory
from app.llm.embeddings import EmbeddingService
from app.llm.router import ModelRouter
from app.services.contract_review import run_contract_review
from app.services.discovery import run_discovery_synthesis
from app.services.drafting import run_draft_generation
from app.services.ingestion import process_document
from app.storage import get_storage_backend
from app.workers.celery_app import celery_app


async def _ingest_async(document_id: str, review_task_id: str | None) -> dict[str, Any]:
    cfg = get_settings()
    engine, factory = build_worker_session_factory(cfg)
    storage = get_storage_backend(cfg)
    embeddings = EmbeddingService(cfg)
    try:
        async with factory() as session:
            doc = await process_document(
                session,
                storage,
                embeddings,
                document_id=uuid.UUID(document_id),
                review_task_id=uuid.UUID(review_task_id) if review_task_id else None,
            )
            return {
                "document_id": str(doc.id),
                "ingestion_status": doc.ingestion_status,
                "chunk_count": doc.chunk_count,
            }
    finally:
        await engine.dispose()


async def _contract_review_async(
    matter_id: str,
    task_id: str,
    document_ids: list[str] | None,
    clause_types: list[str] | None,
    playbook_notes: str | None,
    playbook_id: str | None,
    provider: str | None,
    model: str | None,
) -> dict[str, Any]:
    cfg = get_settings()
    engine, factory = build_worker_session_factory(cfg)
    router = ModelRouter(cfg)
    try:
        async with factory() as session:
            task = await run_contract_review(
                session,
                router,
                matter_id=uuid.UUID(matter_id),
                task_id=uuid.UUID(task_id),
                document_ids=[uuid.UUID(d) for d in document_ids] if document_ids else None,
                clause_types=clause_types,
                playbook_notes=playbook_notes,
                playbook_id=uuid.UUID(playbook_id) if playbook_id else None,
                provider=provider,
                model=model,
                settings=cfg,
            )
            return {"task_id": str(task.id), "status": task.status}
    finally:
        await engine.dispose()


async def _discovery_synthesis_async(
    matter_id: str,
    task_id: str,
    document_ids: list[str] | None,
    focus_topics: list[str] | None,
    provider: str | None,
    model: str | None,
) -> dict[str, Any]:
    cfg = get_settings()
    engine, factory = build_worker_session_factory(cfg)
    router = ModelRouter(cfg)
    try:
        async with factory() as session:
            task = await run_discovery_synthesis(
                session,
                router,
                matter_id=uuid.UUID(matter_id),
                task_id=uuid.UUID(task_id),
                document_ids=[uuid.UUID(d) for d in document_ids] if document_ids else None,
                focus_topics=focus_topics,
                provider=provider,
                model=model,
                settings=cfg,
            )
            return {"task_id": str(task.id), "status": task.status}
    finally:
        await engine.dispose()


async def _draft_generation_async(
    matter_id: str,
    task_id: str,
    draft_type: str,
    instructions: str,
    document_ids: list[str] | None,
    provider: str | None,
    model: str | None,
) -> dict[str, Any]:
    cfg = get_settings()
    engine, factory = build_worker_session_factory(cfg)
    router = ModelRouter(cfg)
    embeddings = EmbeddingService(cfg)
    try:
        async with factory() as session:
            task = await run_draft_generation(
                session,
                router,
                embeddings,
                matter_id=uuid.UUID(matter_id),
                task_id=uuid.UUID(task_id),
                draft_type=draft_type,
                instructions=instructions,
                document_ids=[uuid.UUID(d) for d in document_ids] if document_ids else None,
                provider=provider,
                model=model,
                settings=cfg,
            )
            return {"task_id": str(task.id), "status": task.status}
    finally:
        await engine.dispose()


@celery_app.task(name="ai_paralegal.ingest_document")
def ingest_document_task(document_id: str, review_task_id: str | None = None) -> dict[str, Any]:
    """Celery task to parse, chunk, and embed an uploaded legal document."""
    return asyncio.run(_ingest_async(document_id, review_task_id))


@celery_app.task(name="ai_paralegal.contract_review")
def contract_review_task(
    matter_id: str,
    task_id: str,
    document_ids: list[str] | None = None,
    clause_types: list[str] | None = None,
    playbook_notes: str | None = None,
    playbook_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Celery task to run contract/clause review on a matter."""
    return asyncio.run(
        _contract_review_async(
            matter_id,
            task_id,
            document_ids,
            clause_types,
            playbook_notes,
            playbook_id,
            provider,
            model,
        )
    )


@celery_app.task(name="ai_paralegal.discovery_synthesis")
def discovery_synthesis_task(
    matter_id: str,
    task_id: str,
    document_ids: list[str] | None = None,
    focus_topics: list[str] | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Celery task to synthesize timeline, entities, contradictions, and omissions."""
    return asyncio.run(
        _discovery_synthesis_async(matter_id, task_id, document_ids, focus_topics, provider, model)
    )


@celery_app.task(name="ai_paralegal.draft_generation")
def draft_generation_task(
    matter_id: str,
    task_id: str,
    draft_type: str,
    instructions: str,
    document_ids: list[str] | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Celery task to generate a citation-grounded legal draft."""
    return asyncio.run(
        _draft_generation_async(
            matter_id, task_id, draft_type, instructions, document_ids, provider, model
        )
    )
