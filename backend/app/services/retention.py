import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.metrics import metrics_registry
from app.models.document import Document
from app.models.matter import Matter
from app.models.organization import Organization
from app.schemas.admin import RetentionSweepItem, RetentionSweepReport
from app.services.audit import record_audit_log
from app.services.integrations import dispatch_webhooks
from app.storage.base import StorageBackend


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


async def update_document_retention(
    session: AsyncSession,
    *,
    doc: Document,
    retention_until: datetime | None,
    actor_id: str,
) -> Document:
    """Set or clear a document-specific retention expiration timestamp with audit logging."""
    prev_iso = doc.retention_until.isoformat() if doc.retention_until else None
    doc.retention_until = _to_utc(retention_until) if retention_until is not None else None
    new_iso = doc.retention_until.isoformat() if doc.retention_until else None

    await record_audit_log(
        session,
        matter_id=doc.matter_id,
        document_id=doc.id,
        action="retention_policy_updated",
        actor_type="human",
        actor_id=actor_id,
        previous_state=prev_iso,
        new_state=new_iso,
        notes=(
            f"Updated retention expiration for '{doc.filename}' to {new_iso or 'org default'}."
        ),
    )
    await session.commit()
    await session.refresh(doc)
    return doc


async def run_retention_sweep(
    session: AsyncSession,
    storage: StorageBackend,
    *,
    organization_id: uuid.UUID | None = None,
    as_of: datetime | None = None,
    dry_run: bool = False,
    actor_id: str = "system:retention_worker",
    settings: Settings | None = None,
) -> RetentionSweepReport:
    """Evaluate document retention policies, skipping legal holds and purging expired items."""
    effective_as_of = _to_utc(as_of or datetime.now(UTC))
    cfg = settings or get_settings()

    stmt = (
        select(Document, Matter, Organization)
        .join(Matter, Document.matter_id == Matter.id)
        .outerjoin(Organization, Matter.organization_id == Organization.id)
        .order_by(Document.created_at.asc())
    )
    if organization_id is not None:
        stmt = stmt.where(Matter.organization_id == organization_id)

    rows = (await session.execute(stmt)).all()

    items: list[RetentionSweepItem] = []
    purged_keys: list[str] = []
    purged_count = 0
    eligible_count = 0
    skipped_legal_hold_count = 0
    affected_orgs: set[uuid.UUID | None] = set()

    for doc, matter, org in rows:
        explicit_cutoff = _to_utc(doc.retention_until) if doc.retention_until is not None else None
        effective_cutoff = explicit_cutoff
        if effective_cutoff is None and org is not None and org.retention_days > 0:
            effective_cutoff = _to_utc(doc.created_at) + timedelta(days=org.retention_days)

        if effective_cutoff is None or effective_cutoff > effective_as_of:
            continue

        if doc.legal_hold_active or matter.legal_hold_active:
            skipped_legal_hold_count += 1
            affected_orgs.add(matter.organization_id)
            items.append(
                RetentionSweepItem(
                    document_id=doc.id,
                    matter_id=matter.id,
                    filename=doc.filename,
                    action="skipped_legal_hold",
                    reason="Litigation legal hold prevents purge despite retention expiration.",
                    retention_until=effective_cutoff,
                )
            )
            continue

        if dry_run:
            eligible_count += 1
            items.append(
                RetentionSweepItem(
                    document_id=doc.id,
                    matter_id=matter.id,
                    filename=doc.filename,
                    action="eligible_for_purge",
                    reason=f"Retention window expired at {effective_cutoff.isoformat()}.",
                    retention_until=effective_cutoff,
                )
            )
            continue

        affected_orgs.add(matter.organization_id)
        purged_keys.append(doc.storage_key)
        await record_audit_log(
            session,
            matter_id=matter.id,
            document_id=None,
            action="retention_purged",
            actor_type="system" if actor_id.startswith("system:") else "human",
            actor_id=actor_id,
            previous_state=doc.ingestion_status,
            new_state="purged",
            notes=f"Retention purge removed expired document '{doc.filename}' ({doc.id}).",
            response_metadata={
                "purged_document_id": str(doc.id),
                "filename": doc.filename,
                "storage_key": doc.storage_key,
                "retention_cutoff": effective_cutoff.isoformat(),
            },
        )
        await session.delete(doc)
        purged_count += 1
        items.append(
            RetentionSweepItem(
                document_id=doc.id,
                matter_id=matter.id,
                filename=doc.filename,
                action="purged",
                reason=f"Purged after retention cutoff {effective_cutoff.isoformat()}.",
                retention_until=effective_cutoff,
            )
        )

    if not dry_run and purged_count > 0:
        await session.commit()
        metrics_registry.record_retention_purge(purged_count)
        for key in purged_keys:
            with suppress(Exception):
                await storage.delete(key)

    if not dry_run and (purged_count > 0 or skipped_legal_hold_count > 0):
        for org_id in affected_orgs:
            await dispatch_webhooks(
                session,
                organization_id=org_id,
                matter_id=None,
                event_type="retention.sweep_completed",
                payload={
                    "purged_count": purged_count,
                    "skipped_legal_hold_count": skipped_legal_hold_count,
                    "as_of": effective_as_of.isoformat(),
                    "summary": (
                        f"Retention sweep purged {purged_count} expired document(s) "
                        f"and preserved {skipped_legal_hold_count} document(s) under legal hold."
                    ),
                },
                settings=cfg,
            )

    return RetentionSweepReport(
        organization_id=organization_id,
        dry_run=dry_run,
        as_of=effective_as_of,
        evaluated_count=len(rows),
        purged_count=purged_count,
        eligible_count=eligible_count,
        skipped_legal_hold_count=skipped_legal_hold_count,
        items=items,
    )
