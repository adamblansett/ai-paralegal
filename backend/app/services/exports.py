import csv
import io
import json
import uuid
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.disclaimer import LEGAL_DISCLAIMER
from app.models.finding import ExtractedFinding
from app.models.matter import Matter

ExportFormat = Literal["csv", "json", "markdown"]


def _format_citations_brief(citations: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for cit in citations:
        doc = cit.get("document_filename") or "Unknown doc"
        page = cit.get("page_number")
        para = cit.get("paragraph_index")
        sec = cit.get("section_heading")
        status = cit.get("verification_status") or "unverified"
        loc = doc
        if page is not None:
            loc += f" p.{page}"
        if para is not None:
            loc += f" ¶{para}"
        if sec:
            loc += f" ({sec})"
        quote = str(cit.get("quote") or "").replace("\n", " ")[:120]
        parts.append(f'[{status}] {loc}: "{quote}"')
    return " | ".join(parts)


async def _load_findings_for_export(
    session: AsyncSession,
    *,
    matter_id: uuid.UUID,
    finding_types: list[str] | None = None,
) -> tuple[Matter, list[ExtractedFinding]]:
    matter = await session.get(Matter, matter_id)
    if matter is None:
        raise ValueError(f"Matter {matter_id} not found.")
    stmt = (
        select(ExtractedFinding)
        .where(ExtractedFinding.matter_id == matter_id)
        .order_by(ExtractedFinding.created_at.asc())
    )
    if finding_types:
        stmt = stmt.where(ExtractedFinding.finding_type.in_(finding_types))
    rows = list((await session.execute(stmt)).scalars().all())
    return matter, rows


async def export_review_table(
    session: AsyncSession,
    *,
    matter_id: uuid.UUID,
    fmt: ExportFormat = "csv",
) -> tuple[str, str, str]:
    """Export all matter findings as a structured review table."""
    matter, findings = await _load_findings_for_export(session, matter_id=matter_id)
    slug = matter.matter_number.lower().replace(" ", "_")

    if fmt == "json":
        payload = {
            "matter": {
                "id": str(matter.id),
                "matter_number": matter.matter_number,
                "title": matter.title,
                "client_name": matter.client_name,
            },
            "disclaimer": LEGAL_DISCLAIMER,
            "findings": [
                {
                    "id": str(f.id),
                    "finding_type": f.finding_type,
                    "category": f.category,
                    "title": f.title,
                    "summary": f.edited_summary or f.summary,
                    "severity": f.severity,
                    "confidence": f.confidence,
                    "grounding_score": f.grounding_score,
                    "requires_escalation": f.requires_escalation,
                    "review_status": f.review_status,
                    "reviewer_name": f.reviewer_name,
                    "reviewer_notes": f.reviewer_notes,
                    "citations": f.citations_json,
                }
                for f in findings
            ],
        }
        return json.dumps(payload, indent=2), "application/json", f"{slug}_review_table.json"

    if fmt == "markdown":
        lines = [
            f"# Review Table: {matter.title} ({matter.matter_number})",
            "",
            f"> **Disclaimer:** {LEGAL_DISCLAIMER}",
            "",
            "| Type | Category | Title | Severity | Grounding | Status | Citations |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for f in findings:
            cit_str = _format_citations_brief(f.citations_json).replace("|", "\\|")
            title_clean = f.title.replace("|", "\\|")
            lines.append(
                f"| {f.finding_type} | {f.category} | {title_clean} | {f.severity} | "
                f"{f.grounding_score:.2f} | {f.review_status} | {cit_str} |"
            )
        return "\n".join(lines), "text/markdown", f"{slug}_review_table.md"

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "ID",
            "Finding Type",
            "Category",
            "Title",
            "Summary",
            "Severity",
            "Confidence",
            "Grounding Score",
            "Escalation Required",
            "Review Status",
            "Reviewer",
            "Reviewer Notes",
            "Citations",
        ]
    )
    for f in findings:
        writer.writerow(
            [
                str(f.id),
                f.finding_type,
                f.category,
                f.title,
                f.edited_summary or f.summary,
                f.severity,
                f"{f.confidence:.2f}",
                f"{f.grounding_score:.2f}",
                str(f.requires_escalation),
                f.review_status,
                f.reviewer_name or "",
                f.reviewer_notes or "",
                _format_citations_brief(f.citations_json),
            ]
        )
    return buf.getvalue(), "text/csv", f"{slug}_review_table.csv"


async def export_chronology(
    session: AsyncSession,
    *,
    matter_id: uuid.UUID,
    fmt: ExportFormat = "csv",
) -> tuple[str, str, str]:
    """Export matter timeline/chronology events (`content`, `media_type`, `filename`)."""
    matter, findings = await _load_findings_for_export(
        session, matter_id=matter_id, finding_types=["timeline_event"]
    )
    slug = matter.matter_number.lower().replace(" ", "_")

    if fmt == "json":
        payload = {
            "matter": {
                "id": str(matter.id),
                "matter_number": matter.matter_number,
                "title": matter.title,
            },
            "disclaimer": LEGAL_DISCLAIMER,
            "chronology": [
                {
                    "id": str(f.id),
                    "date_label": (f.metadata_json or {}).get("date_label", ""),
                    "title": f.title,
                    "description": f.edited_summary or f.summary,
                    "review_status": f.review_status,
                    "grounding_score": f.grounding_score,
                    "citations": f.citations_json,
                }
                for f in findings
            ],
        }
        return json.dumps(payload, indent=2), "application/json", f"{slug}_chronology.json"

    if fmt == "markdown":
        lines = [
            f"# Discovery Chronology: {matter.title} ({matter.matter_number})",
            "",
            f"> **Disclaimer:** {LEGAL_DISCLAIMER}",
            "",
            "| Date | Event | Description | Status | Source Citation |",
            "| --- | --- | --- | --- | --- |",
        ]
        for f in findings:
            date_lbl = str((f.metadata_json or {}).get("date_label", ""))
            desc = (f.edited_summary or f.summary).replace("|", "\\|").replace("\n", " ")
            cit = _format_citations_brief(f.citations_json).replace("|", "\\|")
            title_col = f.title.replace("|", "\\|")
            lines.append(
                f"| {date_lbl} | {title_col} | {desc} | {f.review_status} | {cit} |"
            )
        return "\n".join(lines), "text/markdown", f"{slug}_chronology.md"

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["Date", "Title", "Description", "Review Status", "Grounding Score", "Citations"]
    )
    for f in findings:
        date_lbl = str((f.metadata_json or {}).get("date_label", ""))
        writer.writerow(
            [
                date_lbl,
                f.title,
                f.edited_summary or f.summary,
                f.review_status,
                f"{f.grounding_score:.2f}",
                _format_citations_brief(f.citations_json),
            ]
        )
    return buf.getvalue(), "text/csv", f"{slug}_chronology.csv"
