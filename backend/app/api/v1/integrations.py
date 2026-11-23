import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    format_actor_id,
    get_app_settings,
    get_current_user,
    get_db,
    get_embeddings,
    get_storage,
    require_matter_access,
    require_roles,
)
from app.core.config import Settings
from app.core.disclaimer import LEGAL_DISCLAIMER
from app.llm.embeddings import EmbeddingService
from app.models.document import Document
from app.models.finding import ExtractedFinding
from app.models.organization import WebhookSubscription
from app.models.user import User
from app.schemas.document import DocumentRead, DocumentUploadResponse
from app.schemas.integrations import (
    DMSImportRequest,
    WebhookCreate,
    WebhookRead,
    WebhookTestResult,
    WebhookUpdate,
    WordRedlineItem,
    WordRedlinePayloadResponse,
)
from app.services.audit import record_audit_log
from app.services.ingestion import upload_document
from app.services.integrations import (
    DMSFileItem,
    DMSNotConfiguredError,
    DMSProviderInfo,
    get_dms_connector,
    list_dms_providers,
    send_single_webhook,
    validate_webhook_url,
)
from app.storage.base import StorageBackend
from app.workers.dispatch import dispatch_ingestion

router = APIRouter(tags=["integrations"])


@router.get("/integrations/dms/providers", response_model=list[DMSProviderInfo])
async def get_dms_providers(
    current_user: User = Depends(get_current_user),
) -> list[DMSProviderInfo]:
    """List supported enterprise DMS providers and their configuration readiness."""
    _ = current_user
    return list_dms_providers()


@router.get("/integrations/dms/{provider}/files", response_model=list[DMSFileItem])
async def list_dms_files(
    provider: str,
    folder_id: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
) -> list[DMSFileItem]:
    """List importable legal files from a DMS connector."""
    _ = current_user
    try:
        connector = get_dms_connector(provider)
        return await connector.list_files(folder_id=folder_id)
    except DMSNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/matters/{matter_id}/integrations/dms/import",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def import_from_dms(
    matter_id: uuid.UUID,
    payload: DMSImportRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    storage: StorageBackend = Depends(get_storage),
    embeddings: EmbeddingService = Depends(get_embeddings),
) -> DocumentUploadResponse:
    """Import a legal document from a DMS connector into a matter and trigger ingestion."""
    await require_matter_access(session, matter_id=matter_id, user=current_user, write=True)
    try:
        connector = get_dms_connector(payload.provider)
        filename, content_type, raw_bytes = await connector.download_file(payload.external_id)
    except DMSNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    actor = format_actor_id(current_user)
    doc, task = await upload_document(
        session,
        storage,
        matter_id=matter_id,
        filename=filename,
        content_type=content_type,
        data=raw_bytes,
        document_type=payload.document_type,
        actor_id=actor,
    )
    meta = dict(doc.metadata_json or {})
    meta["dms_source_provider"] = payload.provider
    meta["dms_external_id"] = payload.external_id
    doc.metadata_json = meta

    await record_audit_log(
        session,
        matter_id=matter_id,
        document_id=doc.id,
        review_task_id=task.id,
        action="dms_imported",
        actor_type="human",
        actor_id=actor,
        new_state="pending",
        notes=(
            f"Imported {filename} from DMS provider '{payload.provider}' ({payload.external_id})."
        ),
    )
    await session.commit()

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


@router.get("/integrations/webhooks", response_model=list[WebhookRead])
async def list_webhooks(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> list[WebhookRead]:
    """List outbound webhook / Slack / Teams escalation subscriptions for the tenant."""
    stmt = select(WebhookSubscription).order_by(WebhookSubscription.created_at.desc())
    if current_user.organization_id is not None:
        stmt = stmt.where(
            (WebhookSubscription.organization_id == current_user.organization_id)
            | (WebhookSubscription.organization_id.is_(None))
        )
    rows = (await session.execute(stmt)).scalars().all()
    return [WebhookRead.model_validate(r) for r in rows]


@router.post(
    "/integrations/webhooks",
    response_model=WebhookRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_webhook(
    payload: WebhookCreate,
    current_user: User = Depends(require_roles("partner", "associate")),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> WebhookRead:
    """Register an HMAC-signed escalation webhook or Slack/Teams channel notification."""
    if payload.matter_id is not None:
        await require_matter_access(
            session, matter_id=payload.matter_id, user=current_user, write=True
        )
    try:
        clean_url = validate_webhook_url(
            payload.target_url, allow_private_ips=settings.webhook_allow_private_ips
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    sub = WebhookSubscription(
        organization_id=current_user.organization_id,
        matter_id=payload.matter_id,
        name=payload.name.strip(),
        channel_type=payload.channel_type,
        target_url=clean_url,
        signing_secret=payload.signing_secret,
        events_json=payload.events,
        is_active=True,
    )
    session.add(sub)
    await session.commit()
    await session.refresh(sub)
    return WebhookRead.model_validate(sub)


@router.patch("/integrations/webhooks/{webhook_id}", response_model=WebhookRead)
async def update_webhook(
    webhook_id: uuid.UUID,
    payload: WebhookUpdate,
    current_user: User = Depends(require_roles("partner", "associate")),
    session: AsyncSession = Depends(get_db),
) -> WebhookRead:
    """Update webhook name, active status, or subscribed event list."""
    sub = await session.get(WebhookSubscription, webhook_id)
    if sub is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found.")
    if (
        sub.organization_id is not None
        and current_user.organization_id is not None
        and sub.organization_id != current_user.organization_id
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
    if payload.name is not None:
        sub.name = payload.name.strip()
    if payload.is_active is not None:
        sub.is_active = payload.is_active
    if payload.events is not None:
        sub.events_json = payload.events
    await session.commit()
    await session.refresh(sub)
    return WebhookRead.model_validate(sub)


@router.delete("/integrations/webhooks/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    webhook_id: uuid.UUID,
    current_user: User = Depends(require_roles("partner", "associate")),
    session: AsyncSession = Depends(get_db),
) -> Response:
    sub = await session.get(WebhookSubscription, webhook_id)
    if sub is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found.")
    if (
        sub.organization_id is not None
        and current_user.organization_id is not None
        and sub.organization_id != current_user.organization_id
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
    await session.delete(sub)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/integrations/webhooks/{webhook_id}/test", response_model=WebhookTestResult)
async def test_webhook(
    webhook_id: uuid.UUID,
    current_user: User = Depends(require_roles("partner", "associate")),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
) -> WebhookTestResult:
    """Send a signed synthetic test notification to a configured webhook subscription."""
    sub = await session.get(WebhookSubscription, webhook_id)
    if sub is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found.")
    if (
        sub.organization_id is not None
        and current_user.organization_id is not None
        and sub.organization_id != current_user.organization_id
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
    ok, status_code, err = await send_single_webhook(
        sub,
        event_type="webhook.test",
        payload={
            "summary": f"Test escalation alert triggered by {current_user.full_name}",
            "triggered_by": current_user.email,
        },
        settings=settings,
    )
    return WebhookTestResult(delivered=ok, status_code=status_code, error=err)


@router.get("/matters/{matter_id}/redlines/word-payload", response_model=WordRedlinePayloadResponse)
async def get_word_addin_redline_payload(
    matter_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> WordRedlinePayloadResponse:
    """Return structured redline instructions consumed by the Word Office.js task-pane add-in."""
    matter = await require_matter_access(
        session, matter_id=matter_id, user=current_user, write=False
    )
    doc_stmt = select(Document).where(Document.matter_id == matter_id)
    doc_names = {d.id: d.filename for d in (await session.execute(doc_stmt)).scalars().all()}

    finding_stmt = (
        select(ExtractedFinding)
        .where(
            ExtractedFinding.matter_id == matter_id,
            ExtractedFinding.finding_type.in_(["risk_flag", "missing_protection", "clause"]),
        )
        .order_by(ExtractedFinding.created_at.asc())
    )
    findings = (await session.execute(finding_stmt)).scalars().all()

    items: list[WordRedlineItem] = []
    for f in findings:
        meta = f.metadata_json or {}
        cits = f.citations_json or []
        orig = str(cits[0].get("quote", "")) if cits else "[Insert missing protective clause]"
        proposed = str(
            meta.get("proposed_redline")
            or f.edited_summary
            or f.recommended_action
            or f.summary
        )
        fname = doc_names.get(f.document_id) if f.document_id else (
            str(cits[0].get("document_filename")) if cits else None
        )
        items.append(
            WordRedlineItem(
                finding_id=f.id,
                category=f.category,
                severity=f.severity,
                title=f.title,
                review_status=f.review_status,
                original_quote=orig,
                proposed_redline=proposed,
                rationale=f.recommended_action or f.summary,
                document_filename=fname,
            )
        )

    return WordRedlinePayloadResponse(
        matter_id=matter.id,
        matter_title=matter.title,
        items=items,
        disclaimer=LEGAL_DISCLAIMER,
    )
