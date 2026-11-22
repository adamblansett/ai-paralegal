import hashlib
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.llm.embeddings import EmbeddingService
from app.models.document import Document, DocumentChunk
from app.models.review_task import ReviewTask
from app.services.audit import record_audit_log
from app.services.chunker import chunk_segments
from app.services.ocr import OCRService
from app.services.parsers import parse_document_bytes
from app.storage.base import StorageBackend

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize_filename(filename: str) -> str:
    base = Path(filename).name.strip() or "document.txt"
    return _SAFE_FILENAME_RE.sub("_", base)


async def upload_document(
    session: AsyncSession,
    storage: StorageBackend,
    *,
    matter_id: uuid.UUID,
    filename: str,
    content_type: str,
    data: bytes,
    document_type: str = "general",
    actor_id: str = "legal_ops_user",
) -> tuple[Document, ReviewTask]:
    """Store uploaded bytes and create Document + queued ReviewTask records."""
    safe_name = _sanitize_filename(filename)
    doc_id = uuid.uuid4()
    sha256_hash = hashlib.sha256(data).hexdigest()
    storage_key = f"matters/{matter_id}/documents/{doc_id}/{safe_name}"

    stored_key = await storage.put_bytes(storage_key, data, content_type=content_type)

    doc = Document(
        id=doc_id,
        matter_id=matter_id,
        filename=Path(filename).name or safe_name,
        content_type=content_type or "application/octet-stream",
        document_type=document_type or "general",
        storage_backend=storage.backend_name,
        storage_key=stored_key,
        sha256_hash=sha256_hash,
        file_size_bytes=len(data),
        ingestion_status="pending",
    )
    session.add(doc)
    await session.flush()

    task = ReviewTask(
        matter_id=matter_id,
        document_id=doc.id,
        task_type="document_ingestion",
        status="queued",
        input_params_json={"filename": doc.filename, "document_type": doc.document_type},
    )
    session.add(task)
    await session.flush()

    await record_audit_log(
        session,
        matter_id=matter_id,
        document_id=doc.id,
        review_task_id=task.id,
        action="document_uploaded",
        actor_type="human",
        actor_id=actor_id,
        new_state="pending",
        notes=f"Uploaded {doc.filename} ({len(data)} bytes, sha256={sha256_hash[:12]}...)",
    )

    await session.commit()
    await session.refresh(doc)
    await session.refresh(task)
    return doc, task


async def process_document(
    session: AsyncSession,
    storage: StorageBackend,
    embedding_service: EmbeddingService,
    *,
    document_id: uuid.UUID,
    review_task_id: uuid.UUID | None = None,
) -> Document:
    """Parse, chunk, embed, and index an uploaded document into DocumentChunk rows."""
    doc = await session.get(Document, document_id)
    if doc is None:
        raise ValueError(f"Document {document_id} not found.")

    task: ReviewTask | None = None
    if review_task_id is not None:
        task = await session.get(ReviewTask, review_task_id)
    else:
        stmt = (
            select(ReviewTask)
            .where(
                ReviewTask.document_id == document_id,
                ReviewTask.task_type == "document_ingestion",
            )
            .order_by(ReviewTask.created_at.desc())
        )
        task = (await session.execute(stmt)).scalars().first()

    now = datetime.now(UTC)
    doc.ingestion_status = "processing"
    doc.error_message = None
    if task is not None:
        task.status = "running"
        task.started_at = now
        task.error_message = None
    await session.commit()

    try:
        cfg = get_settings()
        ocr_service = OCRService(
            backend_mode=cfg.ocr_backend,
            min_chars_per_page=cfg.ocr_min_chars_per_page,
        )
        raw_bytes = await storage.get_bytes(doc.storage_key)
        hinted = None if doc.document_type == "general" else doc.document_type
        parsed = parse_document_bytes(
            doc.filename,
            raw_bytes,
            content_type=doc.content_type,
            hinted_document_type=hinted,
            ocr_service=ocr_service,
        )
        chunk_drafts = chunk_segments(parsed.segments)
        embeddings = (
            await embedding_service.embed_batch([c.content for c in chunk_drafts])
            if chunk_drafts
            else []
        )

        # Replace any prior chunks cleanly for idempotent reprocessing
        await session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc.id))

        for draft, vector in zip(chunk_drafts, embeddings, strict=True):
            session.add(
                DocumentChunk(
                    document_id=doc.id,
                    matter_id=doc.matter_id,
                    chunk_index=draft.chunk_index,
                    content=draft.content,
                    page_number=draft.page_number,
                    paragraph_index=draft.paragraph_index,
                    section_heading=draft.section_heading,
                    char_start=draft.char_start,
                    char_end=draft.char_end,
                    token_count=draft.token_count,
                    embedding=vector,
                    metadata_json=draft.metadata,
                )
            )

        if doc.document_type == "general" and parsed.detected_document_type != "general":
            doc.document_type = parsed.detected_document_type

        doc.ingestion_status = "indexed"
        doc.page_count = parsed.page_count
        doc.word_count = parsed.word_count
        doc.chunk_count = len(chunk_drafts)
        doc.metadata_json = {**(doc.metadata_json or {}), **parsed.metadata}

        completed = datetime.now(UTC)
        if task is not None:
            task.status = "completed"
            task.completed_at = completed
            task.result_json = {
                "page_count": doc.page_count,
                "word_count": doc.word_count,
                "chunk_count": doc.chunk_count,
                "detected_document_type": doc.document_type,
            }

        await record_audit_log(
            session,
            matter_id=doc.matter_id,
            document_id=doc.id,
            review_task_id=task.id if task else None,
            action="document_indexed",
            actor_type="worker",
            actor_id="ingestion_pipeline",
            previous_state="processing",
            new_state="indexed",
            notes=(
                f"Indexed {doc.chunk_count} chunk(s), {doc.page_count} page(s), "
                f"{doc.word_count} word(s)."
            ),
        )
        await session.commit()
        await session.refresh(doc)
        return doc
    except Exception as exc:
        await session.rollback()
        doc_err = await session.get(Document, document_id)
        if doc_err is not None:
            doc_err.ingestion_status = "failed"
            doc_err.error_message = str(exc)
            if review_task_id is not None:
                task_err = await session.get(ReviewTask, review_task_id)
                if task_err is not None:
                    task_err.status = "failed"
                    task_err.error_message = str(exc)
                    task_err.completed_at = datetime.now(UTC)
            await record_audit_log(
                session,
                matter_id=doc_err.matter_id,
                document_id=doc_err.id,
                review_task_id=review_task_id,
                action="document_failed",
                actor_type="worker",
                actor_id="ingestion_pipeline",
                previous_state="processing",
                new_state="failed",
                notes=str(exc),
            )
            await session.commit()
        raise
