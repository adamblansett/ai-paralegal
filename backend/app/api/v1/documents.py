import uuid
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    format_actor_id,
    get_app_settings,
    get_current_user,
    get_db,
    get_embeddings,
    get_storage,
    require_document_access,
    require_matter_access,
)
from app.core.config import Settings
from app.llm.embeddings import EmbeddingService
from app.models.document import Document, DocumentChunk
from app.models.review_task import ReviewTask
from app.models.user import User
from app.schemas.admin import DocumentRetentionUpdate
from app.schemas.document import (
    BatesStampRequest,
    BatesStampResponse,
    DocumentChunkRead,
    DocumentPrivilegeUpdate,
    DocumentRead,
    DocumentUploadResponse,
)
from app.services.ingestion import upload_document
from app.services.production import apply_bates_stamps, update_document_privilege
from app.services.retention import update_document_retention
from app.storage.base import StorageBackend
from app.workers.dispatch import dispatch_ingestion

router = APIRouter(tags=["documents"])
_UPLOAD_CHUNK_BYTES = 64 * 1024


async def _read_validated_upload(file: UploadFile, settings: Settings) -> tuple[str, bytes]:
    filename = Path(file.filename or "uploaded_document.txt").name or "uploaded_document.txt"
    ext = Path(filename).suffix.lower()
    allowed = settings.allowed_upload_extension_set
    if not ext or ext not in allowed:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"File extension '{ext or '(none)'}' is not permitted. "
                f"Allowed extensions: {', '.join(sorted(allowed))}."
            ),
        )

    max_bytes = settings.max_upload_size_bytes
    chunks: list[bytes] = []
    total = 0
    while True:
        piece = await file.read(_UPLOAD_CHUNK_BYTES)
        if not piece:
            break
        total += len(piece)
        if total > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=(
                    f"Uploaded file exceeds maximum allowed size of "
                    f"{settings.max_upload_size_mb} MB."
                ),
            )
        chunks.append(piece)

    raw_bytes = b"".join(chunks)
    if not raw_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    # Magic-byte validation for binary formats to reject spoofed extensions
    if ext == ".pdf" and not raw_bytes.lstrip()[:5].startswith(b"%PDF-"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid PDF signature: file header does not start with '%PDF-'.",
        )
    if ext == ".docx" and not raw_bytes[:2].startswith(b"PK"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid DOCX signature: expected OOXML ZIP archive header.",
        )

    return filename, raw_bytes


@router.post(
    "/matters/{matter_id}/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_matter_document(
    matter_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    document_type: str = Form(default="general"),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    storage: StorageBackend = Depends(get_storage),
    embeddings: EmbeddingService = Depends(get_embeddings),
) -> DocumentUploadResponse:
    """Upload a validated document to a matter and trigger parsing, chunking, and embedding."""
    await require_matter_access(session, matter_id=matter_id, user=current_user, write=True)
    filename, raw_bytes = await _read_validated_upload(file, settings)

    doc, task = await upload_document(
        session,
        storage,
        matter_id=matter_id,
        filename=filename,
        content_type=file.content_type or "application/octet-stream",
        data=raw_bytes,
        document_type=document_type,
        actor_id=format_actor_id(current_user),
    )

    await dispatch_ingestion(
        session=session,
        settings=settings,
        background_tasks=background_tasks,
        storage=storage,
        embeddings=embeddings,
        document_id=doc.id,
        task=task,
    )
    await session.refresh(doc)
    return DocumentUploadResponse(
        document=DocumentRead.model_validate(doc),
        task_id=task.id,
        ingestion_status=doc.ingestion_status,
    )


@router.get("/matters/{matter_id}/documents", response_model=list[DocumentRead])
async def list_matter_documents(
    matter_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> list[DocumentRead]:
    """List documents uploaded to a matter."""
    await require_matter_access(session, matter_id=matter_id, user=current_user, write=False)
    stmt = (
        select(Document).where(Document.matter_id == matter_id).order_by(Document.created_at.desc())
    )
    docs = (await session.execute(stmt)).scalars().all()
    return [DocumentRead.model_validate(d) for d in docs]


@router.get("/documents/{document_id}", response_model=DocumentRead)
async def get_document(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> DocumentRead:
    """Get document metadata and ingestion status."""
    doc = await require_document_access(
        session, document_id=document_id, user=current_user, write=False
    )
    return DocumentRead.model_validate(doc)


@router.get("/documents/{document_id}/content")
async def get_document_content(
    document_id: uuid.UUID,
    download: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> Response:
    """Serve stored document bytes inline or as attachment for Source Viewer inspection."""
    doc = await require_document_access(
        session, document_id=document_id, user=current_user, write=False
    )
    try:
        raw_bytes = await storage.get_bytes(doc.storage_key)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Stored document content could not be located.",
        ) from exc

    safe_name = doc.filename.replace('"', "").replace("\r", "").replace("\n", "")
    disposition = "attachment" if download else "inline"
    return Response(
        content=raw_bytes,
        media_type=doc.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": f'{disposition}; filename="{safe_name}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/documents/{document_id}/download-url")
async def get_document_download_url(
    document_id: uuid.UUID,
    expires_in: int = Query(default=300, ge=30, le=3600),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    storage: StorageBackend = Depends(get_storage),
) -> dict[str, object]:
    """Return an S3 presigned URL (when on S3) or API proxy content URL."""
    doc = await require_document_access(
        session, document_id=document_id, user=current_user, write=False
    )
    presigned = await storage.generate_presigned_url(
        doc.storage_key,
        expires_in=expires_in,
        download_filename=doc.filename,
    )
    if presigned:
        return {
            "document_id": str(doc.id),
            "mode": "s3_presigned",
            "url": presigned,
            "expires_in": expires_in,
        }
    return {
        "document_id": str(doc.id),
        "mode": "api_proxy",
        "url": f"{settings.api_v1_prefix}/documents/{doc.id}/content?download=true",
        "expires_in": expires_in,
    }


@router.get("/documents/{document_id}/chunks", response_model=list[DocumentChunkRead])
async def list_document_chunks(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> list[DocumentChunkRead]:
    """Inspect provenance-preserving parsed chunks for a document."""
    await require_document_access(
        session, document_id=document_id, user=current_user, write=False
    )
    stmt = (
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.chunk_index.asc())
    )
    chunks = (await session.execute(stmt)).scalars().all()
    return [DocumentChunkRead.model_validate(c) for c in chunks]


@router.patch("/documents/{document_id}/privilege", response_model=DocumentRead)
async def set_document_privilege(
    document_id: uuid.UUID,
    payload: DocumentPrivilegeUpdate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> DocumentRead:
    """Update privilege/confidentiality classification on a matter document."""
    doc = await require_document_access(
        session, document_id=document_id, user=current_user, write=True
    )
    updated = await update_document_privilege(
        session,
        doc=doc,
        privilege_status=payload.privilege_status,
        privilege_reason=payload.privilege_reason,
        actor_id=format_actor_id(current_user),
    )
    return DocumentRead.model_validate(updated)


@router.patch("/documents/{document_id}/retention", response_model=DocumentRead)
async def set_document_retention(
    document_id: uuid.UUID,
    payload: DocumentRetentionUpdate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> DocumentRead:
    """Set or clear a custom retention expiration timestamp for a matter document."""
    doc = await require_document_access(
        session, document_id=document_id, user=current_user, write=True
    )
    updated = await update_document_retention(
        session,
        doc=doc,
        retention_until=payload.retention_until,
        actor_id=format_actor_id(current_user),
    )
    return DocumentRead.model_validate(updated)


@router.post("/matters/{matter_id}/documents/bates-stamp", response_model=BatesStampResponse)
async def bates_stamp_matter_documents(
    matter_id: uuid.UUID,
    payload: BatesStampRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> BatesStampResponse:
    """Apply sequential page-level Bates numbering across matter documents."""
    await require_matter_access(session, matter_id=matter_id, user=current_user, write=True)
    stamped_docs, next_num = await apply_bates_stamps(
        session,
        matter_id=matter_id,
        prefix=payload.prefix,
        start_number=payload.start_number,
        zero_pad=payload.zero_pad,
        document_ids=payload.document_ids,
        actor_id=format_actor_id(current_user),
    )
    return BatesStampResponse(
        matter_id=matter_id,
        prefix=payload.prefix,
        stamped_count=len(stamped_docs),
        next_number=next_num,
        documents=[DocumentRead.model_validate(d) for d in stamped_docs],
    )


@router.post("/documents/{document_id}/reprocess", response_model=DocumentUploadResponse)
async def reprocess_document(
    document_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    storage: StorageBackend = Depends(get_storage),
    embeddings: EmbeddingService = Depends(get_embeddings),
) -> DocumentUploadResponse:
    """Re-run parsing, chunking, and embedding on an existing document."""
    doc = await require_document_access(
        session, document_id=document_id, user=current_user, write=True
    )

    task = ReviewTask(
        matter_id=doc.matter_id,
        document_id=doc.id,
        task_type="document_ingestion",
        status="queued",
        input_params_json={"reprocess": True, "filename": doc.filename},
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)

    await dispatch_ingestion(
        session=session,
        settings=settings,
        background_tasks=background_tasks,
        storage=storage,
        embeddings=embeddings,
        document_id=doc.id,
        task=task,
    )
    await session.refresh(doc)
    return DocumentUploadResponse(
        document=DocumentRead.model_validate(doc),
        task_id=task.id,
        ingestion_status=doc.ingestion_status,
    )


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> Response:
    """Delete a document, its stored bytes, and its indexed chunks (blocked on legal hold)."""
    from app.models.matter import Matter

    doc = await require_document_access(
        session, document_id=document_id, user=current_user, write=True
    )
    matter = await session.get(Matter, doc.matter_id)
    if doc.legal_hold_active or (matter is not None and matter.legal_hold_active):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete document while litigation legal hold is active.",
        )
    storage_key = doc.storage_key
    await session.delete(doc)
    await session.commit()
    await storage.delete(storage_key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
