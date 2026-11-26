import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.document import Document, DocumentChunk
from app.models.legal_ops import MatterObligation
from app.models.matter import Matter
from app.schemas.legal_ops import ObligationAlertResult, ObligationRead
from app.services.audit import record_audit_log
from app.services.integrations import dispatch_webhooks

_MONTHS: dict[str, int] = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_LONG_DATE_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2}),\s+(\d{4})\b"
)
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_NOTICE_RE = re.compile(
    r"(\d{1,3})\s*(?:calendar|business)?\s*days?(?:'|\s+prior)?\s*(?:written\s+)?notice",
    re.IGNORECASE,
)
_CURE_RE = re.compile(
    r"(\d{1,3})\s*(?:calendar|business)?\s*days?\s*(?:to\s+cure|cure\s+period)",
    re.IGNORECASE,
)


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _parse_first_date(sentence: str) -> datetime | None:
    m_iso = _ISO_DATE_RE.search(sentence)
    if m_iso:
        try:
            return datetime(
                int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)), tzinfo=UTC
            )
        except ValueError:
            pass
    m_long = _LONG_DATE_RE.search(sentence)
    if m_long:
        month = _MONTHS.get(m_long.group(1).lower())
        if month:
            try:
                return datetime(int(m_long.group(3)), month, int(m_long.group(2)), tzinfo=UTC)
            except ValueError:
                pass
    return None


def _ics_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


async def extract_obligations_from_matter(
    session: AsyncSession,
    *,
    matter_id: uuid.UUID,
    actor_id: str,
) -> list[MatterObligation]:
    """Scan matter chunks for renewal notices, cure windows, and explicit contractual deadlines."""
    docs = {
        d.id: d
        for d in (
            await session.execute(select(Document).where(Document.matter_id == matter_id))
        )
        .scalars()
        .all()
    }
    chunks = list(
        (
            await session.execute(
                select(DocumentChunk)
                .where(DocumentChunk.matter_id == matter_id)
                .order_by(DocumentChunk.chunk_index.asc())
            )
        )
        .scalars()
        .all()
    )
    existing_titles = {
        o.title.strip().lower()
        for o in (
            await session.execute(
                select(MatterObligation).where(MatterObligation.matter_id == matter_id)
            )
        )
        .scalars()
        .all()
    }

    now = datetime.now(UTC)
    created: list[MatterObligation] = []

    for chunk in chunks:
        doc = docs.get(chunk.document_id)
        sentences = [
            s.strip()
            for s in re.split(r"(?<=[.!?])\s+|\n+", chunk.content)
            if s.strip()
        ]
        for sentence in sentences:
            lower = sentence.lower()
            parsed_date = _parse_first_date(sentence)
            m_notice = _NOTICE_RE.search(sentence)
            m_cure = _CURE_RE.search(sentence)

            if m_notice and any(k in lower for k in ("renew", "terminat", "non-renew", "cancel")):
                days = int(m_notice.group(1))
                is_renewal = "renew" in lower
                category = "renewal_notice" if is_renewal else "termination_window"
                title = (
                    f"{'Renewal' if is_renewal else 'Termination'} notice deadline "
                    f"({days} days prior)"
                )
                if doc:
                    title = f"{doc.filename}: {title}"
                title = title[:255]
                if title.lower() in existing_titles:
                    continue
                due_dt = parsed_date or (now + timedelta(days=max(7, min(days, 180))))
                ob = MatterObligation(
                    matter_id=matter_id,
                    document_id=chunk.document_id,
                    title=title,
                    category=category,
                    due_date=due_dt,
                    notice_window_days=days,
                    status="open",
                    source_quote=sentence[:1000],
                    notes=f"Auto-extracted from {chunk.section_heading or 'document text'}.",
                )
                session.add(ob)
                created.append(ob)
                existing_titles.add(title.lower())
            elif m_cure:
                days = int(m_cure.group(1))
                title = f"Breach cure period window ({days} days)"
                if doc:
                    title = f"{doc.filename}: {title}"
                title = title[:255]
                if title.lower() in existing_titles:
                    continue
                ob = MatterObligation(
                    matter_id=matter_id,
                    document_id=chunk.document_id,
                    title=title,
                    category="cure_period",
                    due_date=parsed_date or (now + timedelta(days=days)),
                    notice_window_days=days,
                    status="open",
                    source_quote=sentence[:1000],
                    notes="Auto-extracted breach cure window.",
                )
                session.add(ob)
                created.append(ob)
                existing_titles.add(title.lower())
            elif parsed_date is not None and any(
                k in lower
                for k in (
                    "due",
                    "deadline",
                    "expir",
                    "deliver",
                    "filing",
                    "payment",
                    "invoice",
                    "renew",
                )
            ):
                category = "filing_deadline" if "fil" in lower else (
                    "payment" if any(p in lower for p in ("pay", "invoice", "$")) else "compliance"
                )
                prefix = doc.filename if doc else "Matter"
                title = f"{prefix}: Obligation due {parsed_date.strftime('%Y-%m-%d')}"[:255]
                if title.lower() in existing_titles:
                    continue
                ob = MatterObligation(
                    matter_id=matter_id,
                    document_id=chunk.document_id,
                    title=title,
                    category=category,
                    due_date=parsed_date,
                    status="open",
                    source_quote=sentence[:1000],
                    notes="Extracted explicit contractual or discovery deadline.",
                )
                session.add(ob)
                created.append(ob)
                existing_titles.add(title.lower())

    if created:
        await session.flush()
        await record_audit_log(
            session,
            matter_id=matter_id,
            action="obligations_extracted",
            actor_type="human",
            actor_id=actor_id,
            new_state="open",
            notes=f"Extracted {len(created)} legal obligation(s) and notice deadline(s).",
        )
        await session.commit()
        for ob in created:
            await session.refresh(ob)

    return created


def render_obligations_ics(matter: Matter, obligations: list[MatterObligation]) -> str:
    """Render matter obligations as an RFC 5545 iCalendar (`.ics`) feed with 7-day reminders."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//AI Paralegal//Legal Obligations Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ics_escape(matter.matter_number + ' Obligations')}",
    ]
    for ob in obligations:
        due_utc = _to_utc(ob.due_date)
        dt_start = due_utc.strftime("%Y%m%d")
        dt_end = (due_utc + timedelta(days=1)).strftime("%Y%m%d")
        desc = f"Matter: {matter.title} ({matter.matter_number}) | Category: {ob.category}"
        if ob.source_quote:
            desc += f" | Source: {ob.source_quote}"
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{ob.id}@ai-paralegal.local",
                f"DTSTAMP:{stamp}",
                f"DTSTART;VALUE=DATE:{dt_start}",
                f"DTEND;VALUE=DATE:{dt_end}",
                f"SUMMARY:{_ics_escape('[' + ob.category.upper() + '] ' + ob.title)}",
                f"DESCRIPTION:{_ics_escape(desc)}",
                f"STATUS:{'COMPLETED' if ob.status == 'completed' else 'CONFIRMED'}",
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                "DESCRIPTION:Legal obligation deadline approaching",
                "TRIGGER:-P7D",
                "END:VALARM",
                "END:VEVENT",
            ]
        )
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


async def check_and_alert_obligations(
    session: AsyncSession,
    *,
    matter: Matter,
    due_within_days: int = 14,
    settings: Settings | None = None,
) -> ObligationAlertResult:
    """Flag overdue obligations and dispatch signed webhooks for approaching deadlines."""
    cfg = settings or get_settings()
    now = datetime.now(UTC)
    horizon = now + timedelta(days=due_within_days)

    rows = list(
        (
            await session.execute(
                select(MatterObligation)
                .where(MatterObligation.matter_id == matter.id)
                .order_by(MatterObligation.due_date.asc())
            )
        )
        .scalars()
        .all()
    )

    alerted: list[MatterObligation] = []
    overdue_count = 0
    for ob in rows:
        if ob.status in {"completed", "waived"}:
            continue
        due_utc = _to_utc(ob.due_date)
        if due_utc < now:
            if ob.status != "overdue":
                ob.status = "overdue"
            overdue_count += 1
            alerted.append(ob)
        elif due_utc <= horizon:
            alerted.append(ob)

    if overdue_count > 0:
        await session.commit()

    webhooks_dispatched = 0
    if alerted:
        webhooks_dispatched = await dispatch_webhooks(
            session,
            organization_id=matter.organization_id,
            matter_id=matter.id,
            event_type="obligation.deadline_alert",
            payload={
                "matter_id": str(matter.id),
                "matter_title": matter.title,
                "overdue_count": overdue_count,
                "due_soon_count": len(alerted) - overdue_count,
                "summary": (
                    f"{overdue_count} overdue and {len(alerted) - overdue_count} upcoming "
                    f"obligation(s) within {due_within_days} days."
                ),
            },
            settings=cfg,
        )

    return ObligationAlertResult(
        matter_id=matter.id,
        due_within_days=due_within_days,
        alerted_count=len(alerted),
        overdue_count=overdue_count,
        webhooks_dispatched=webhooks_dispatched,
        obligations=[ObligationRead.model_validate(o) for o in alerted],
    )
