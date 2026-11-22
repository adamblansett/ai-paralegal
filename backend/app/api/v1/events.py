import asyncio
import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_matter_access
from app.models.document import Document
from app.models.review_task import ReviewTask
from app.models.user import User
from app.services.events import event_bus

router = APIRouter(tags=["events"])


def _format_sse(event_name: str, payload: dict[str, object]) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload, default=str)}\n\n"


@router.get("/matters/{matter_id}/events")
async def stream_matter_events(
    matter_id: uuid.UUID,
    request: Request,
    once: bool = Query(default=False, description="Emit initial snapshot and close stream."),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Server-Sent Events (SSE) stream of live document ingestion & task progress."""
    await require_matter_access(session, matter_id=matter_id, user=current_user, write=False)

    doc_rows = (
        await session.execute(
            select(Document)
            .where(Document.matter_id == matter_id)
            .order_by(Document.created_at.desc())
        )
    ).scalars().all()
    task_rows = (
        await session.execute(
            select(ReviewTask)
            .where(ReviewTask.matter_id == matter_id)
            .order_by(ReviewTask.created_at.desc())
            .limit(20)
        )
    ).scalars().all()

    snapshot_payload: dict[str, object] = {
        "matter_id": str(matter_id),
        "documents": [
            {
                "id": str(d.id),
                "filename": d.filename,
                "ingestion_status": d.ingestion_status,
                "chunk_count": d.chunk_count,
                "privilege_status": d.privilege_status,
                "bates_start": d.bates_start,
                "bates_end": d.bates_end,
            }
            for d in doc_rows
        ],
        "tasks": [
            {
                "id": str(t.id),
                "task_type": t.task_type,
                "status": t.status,
                "resolved_provider": t.resolved_provider,
                "resolved_model": t.resolved_model,
            }
            for t in task_rows
        ],
    }

    async def event_generator() -> AsyncIterator[str]:
        yield _format_sse("snapshot", snapshot_payload)
        if once:
            return

        async with event_bus.subscribe(matter_id) as queue:
            while not await request.is_disconnected():
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield _format_sse(str(evt["event"]), dict(evt["data"]))
                except TimeoutError:
                    yield ": keepalive\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
