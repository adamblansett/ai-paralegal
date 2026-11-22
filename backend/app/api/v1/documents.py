import uuid

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_app_settings, get_db, get_embeddings, get_storage
from app.core.config import Settings
from app.llm.embeddings import EmbeddingService
from app.models.document import Document, DocumentChunk
from app.models.matter import Matter
from app.models.review_task import ReviewTask
from app.schemas.document import DocumentChunkRead, DocumentRead, DocumentUploadResponse
from app.services.ingestion import upload_document
from app.storage.base import StorageBackend
from app.workers.dispatch import dispatch_ingestion

router = APIRouter(tags=["documents"])


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
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    storage: StorageBackend = Depends(get_storage),
    embeddings: EmbeddingService = Depends(get_embeddings),
) -> DocumentUploadResponse:
    """Upload a document to a matter and trigger parsing, chunking, and embedding."""
    matter = await session.get(Matter, matter_id)
    if matter is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Matter not found.")

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    doc, task = await upload_document(
        session,
        storage,
        matter_id=matter_id,
        filename=file.filename or "uploaded_document.txt",
        content_type=file.content_type or "application/octet-stream",
        data=raw_bytes,
        document_type=document_type,
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
    session: AsyncSession = Depends(get_db),
) -> list[DocumentRead]:
    """List documents uploaded to a matter."""
    matter = await session.get(Matter, matter_id)
    if matter is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Matter not found.")
    stmt = (
        select(Document).where(Document.matter_id == matter_id).order_by(Document.created_at.desc())
    )
    docs = (await session.execute(stmt)).scalars().all()
    return [DocumentRead.model_validate(d) for d in docs]


@router.get("/documents/{document_id}", response_model=DocumentRead)
async def get_document(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> DocumentRead:
    """Get document metadata and ingestion status."""
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    return DocumentRead.model_validate(doc)


@router.get("/documents/{document_id}/chunks", response_model=list[DocumentChunkRead])
async def list_document_chunks(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> list[DocumentChunkRead]:
    """Inspect provenance-preserving parsed chunks for a document."""
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    stmt = (
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.chunk_index.asc())
    )
    chunks = (await session.execute(stmt)).scalars().all()
    return [DocumentChunkRead.model_validate(c) for c in chunks]


@router.post("/documents/{document_id}/reprocess", response_model=DocumentUploadResponse)
async def reprocess_document(
    document_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    storage: StorageBackend = Depends(get_storage),
    embeddings: EmbeddingService = Depends(get_embeddings),
) -> DocumentUploadResponse:
    """Re-run parsing, chunking, and embedding on an existing document."""
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

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
    session: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> Response:
    """Delete a document, its stored bytes, and its indexed chunks."""
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    storage_key = doc.storage_key
    await session.delete(doc)
    await session.commit()
    await storage.delete(storage_key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
