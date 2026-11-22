import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentChunk
from app.services.audit import record_audit_log
from app.services.guardrails import PRIVILEGE_STATUSES


async def update_document_privilege(
    session: AsyncSession,
    *,
    doc: Document,
    privilege_status: str,
    privilege_reason: str | None,
    actor_id: str,
) -> Document:
    """Update a document's privilege classification and log the change in the audit trail."""
    if privilege_status not in PRIVILEGE_STATUSES:
        raise ValueError(f"Unsupported privilege status: {privilege_status}")

    prev = doc.privilege_status
    doc.privilege_status = privilege_status
    doc.privilege_reason = privilege_reason.strip() if privilege_reason else None
    meta = dict(doc.metadata_json or {})
    meta["privilege_auto_detected"] = False
    doc.metadata_json = meta

    await record_audit_log(
        session,
        matter_id=doc.matter_id,
        document_id=doc.id,
        action="document_privilege_updated",
        actor_type="human",
        actor_id=actor_id,
        previous_state=prev,
        new_state=privilege_status,
        notes=doc.privilege_reason or f"Privilege set to '{privilege_status}' on {doc.filename}.",
    )
    await session.commit()
    await session.refresh(doc)
    return doc


async def apply_bates_stamps(
    session: AsyncSession,
    *,
    matter_id: uuid.UUID,
    prefix: str = "PROD",
    start_number: int = 1,
    zero_pad: int = 6,
    document_ids: list[uuid.UUID] | None = None,
    actor_id: str = "legal_ops_user",
) -> tuple[list[Document], int]:
    """Assign sequential Bates numbers across documents and their page-aligned chunks."""
    stmt = (
        select(Document)
        .where(Document.matter_id == matter_id)
        .order_by(Document.created_at.asc(), Document.filename.asc())
    )
    if document_ids:
        stmt = stmt.where(Document.id.in_(document_ids))

    docs = list((await session.execute(stmt)).scalars().all())
    if not docs:
        return [], start_number

    cursor = start_number
    for doc in docs:
        pages = max(1, doc.page_count or 1)
        doc_start_num = cursor
        doc_end_num = cursor + pages - 1
        doc.bates_start = f"{prefix}-{doc_start_num:0{zero_pad}d}"
        doc.bates_end = f"{prefix}-{doc_end_num:0{zero_pad}d}"

        chunk_stmt = (
            select(DocumentChunk)
            .where(DocumentChunk.document_id == doc.id)
            .order_by(DocumentChunk.chunk_index.asc())
        )
        chunks = (await session.execute(chunk_stmt)).scalars().all()
        for chunk in chunks:
            page_offset = max(1, min(pages, chunk.page_number or 1)) - 1
            bates_num = f"{prefix}-{(doc_start_num + page_offset):0{zero_pad}d}"
            c_meta = dict(chunk.metadata_json or {})
            c_meta["bates_number"] = bates_num
            chunk.metadata_json = c_meta

        cursor += pages

    await record_audit_log(
        session,
        matter_id=matter_id,
        action="bates_stamped",
        actor_type="human",
        actor_id=actor_id,
        notes=(
            f"Applied Bates stamps ({prefix}-{start_number:0{zero_pad}d} through "
            f"{prefix}-{cursor - 1:0{zero_pad}d}) across {len(docs)} document(s)."
        ),
        response_metadata={
            "prefix": prefix,
            "start_number": start_number,
            "end_number": cursor - 1,
            "document_count": len(docs),
        },
    )
    await session.commit()
    for doc in docs:
        await session.refresh(doc)
    return docs, cursor
