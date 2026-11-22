import csv
import io
import json
import uuid
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.disclaimer import LEGAL_DISCLAIMER
from app.models.document import Document
from app.models.finding import ExtractedFinding
from app.models.matter import Matter
from app.services.contract_review import build_portfolio_risk_matrix
from app.services.redline_export import (
    DOCX_MIME,
    build_ooxml_docx,
    docx_paragraph,
    docx_table,
)

ExportFormat = Literal["csv", "json", "markdown"]
PrivilegeExportFormat = Literal["csv", "json", "markdown", "docx"]


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


async def export_privilege_log(
    session: AsyncSession,
    *,
    matter_id: uuid.UUID,
    fmt: PrivilegeExportFormat = "csv",
) -> tuple[bytes | str, str, str]:
    """Export privilege log entries for all privileged/confidential matter documents."""
    matter = await session.get(Matter, matter_id)
    if matter is None:
        raise ValueError(f"Matter {matter_id} not found.")

    stmt = (
        select(Document)
        .where(
            Document.matter_id == matter_id,
            Document.privilege_status != "none",
        )
        .order_by(Document.bates_start.asc(), Document.created_at.asc())
    )
    docs = list((await session.execute(stmt)).scalars().all())
    slug = matter.matter_number.lower().replace(" ", "_")

    def _bates_range(d: Document) -> str:
        if d.bates_start and d.bates_end:
            return f"{d.bates_start} - {d.bates_end}"
        return d.bates_start or "Unstamped"

    if fmt == "json":
        payload = {
            "matter": {
                "id": str(matter.id),
                "matter_number": matter.matter_number,
                "title": matter.title,
            },
            "disclaimer": LEGAL_DISCLAIMER,
            "entries": [
                {
                    "document_id": str(d.id),
                    "bates_range": _bates_range(d),
                    "filename": d.filename,
                    "document_type": d.document_type,
                    "privilege_status": d.privilege_status,
                    "privilege_reason": d.privilege_reason or "",
                    "pii_detected_count": d.pii_detected_count,
                    "page_count": d.page_count,
                    "sha256_hash": d.sha256_hash,
                }
                for d in docs
            ],
        }
        return json.dumps(payload, indent=2), "application/json", f"{slug}_privilege_log.json"

    if fmt == "markdown":
        lines = [
            f"# Privilege Log: {matter.title} ({matter.matter_number})",
            "",
            f"> **Disclaimer:** {LEGAL_DISCLAIMER}",
            "",
            "| Bates Range | Document | Type | Privilege Basis | "
            "Reason / Description | PII Count | Pages |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for d in docs:
            reason = (d.privilege_reason or "").replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {_bates_range(d)} | {d.filename.replace('|', '\\|')} | {d.document_type} | "
                f"{d.privilege_status} | {reason} | {d.pii_detected_count} | {d.page_count} |"
            )
        return "\n".join(lines), "text/markdown", f"{slug}_privilege_log.md"

    if fmt == "docx":
        body_xml = [
            docx_paragraph(
                f"Privilege Log: {matter.title} ({matter.matter_number})",
                bold=True,
                size=28,
            ),
            docx_paragraph(
                f"Client: {matter.client_name} | Total Privileged Documents: {len(docs)}",
                color="475569",
                size=18,
            ),
            docx_paragraph(f"LEGAL NOTICE: {LEGAL_DISCLAIMER}", bold=True, color="92400E", size=16),
        ]
        if not docs:
            body_xml.append(
                docx_paragraph(
                    "No documents are currently marked as privileged or confidential.",
                    size=18,
                )
            )
        else:
            headers = [
                "Bates Range",
                "Filename",
                "Privilege Basis",
                "Reason / Basis",
                "PII",
                "Pages",
            ]
            table_rows = [
                [
                    _bates_range(d),
                    d.filename,
                    d.privilege_status.replace("_", " ").title(),
                    d.privilege_reason or "Counsel designation",
                    str(d.pii_detected_count),
                    str(d.page_count),
                ]
                for d in docs
            ]
            body_xml.append(
                docx_table(
                    headers,
                    table_rows,
                    col_widths=[1600, 1900, 1600, 2660, 800, 800],
                )
            )
        return build_ooxml_docx(body_xml), DOCX_MIME, f"{slug}_privilege_log.docx"

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "Bates Range",
            "Document ID",
            "Filename",
            "Document Type",
            "Privilege Basis",
            "Privilege Reason",
            "PII Detected Count",
            "Pages",
            "SHA-256",
        ]
    )
    for d in docs:
        writer.writerow(
            [
                _bates_range(d),
                str(d.id),
                d.filename,
                d.document_type,
                d.privilege_status,
                d.privilege_reason or "",
                d.pii_detected_count,
                d.page_count,
                d.sha256_hash,
            ]
        )
    return buf.getvalue(), "text/csv", f"{slug}_privilege_log.csv"


async def export_risk_matrix(
    session: AsyncSession,
    *,
    matter_id: uuid.UUID,
    fmt: Literal["csv", "json"] = "csv",
) -> tuple[str, str, str]:
    """Export portfolio contract risk matrix (documents × clause categories)."""
    matter = await session.get(Matter, matter_id)
    if matter is None:
        raise ValueError(f"Matter {matter_id} not found.")

    matrix = await build_portfolio_risk_matrix(session, matter_id=matter_id)
    slug = matter.matter_number.lower().replace(" ", "_")

    if fmt == "json":
        payload = {
            "matter": {
                "id": str(matter.id),
                "matter_number": matter.matter_number,
                "title": matter.title,
            },
            "disclaimer": LEGAL_DISCLAIMER,
            "matrix": matrix.model_dump(mode="json"),
        }
        return json.dumps(payload, indent=2), "application/json", f"{slug}_risk_matrix.json"

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "Document",
            "Bates Range",
            "Privilege",
            "Overall Risk",
            "Findings",
            "Escalated",
            *[c.replace("_", " ").title() for c in matrix.categories],
        ]
    )
    for row in matrix.rows:
        cell_cols = []
        for cat in matrix.categories:
            cell = row.cells.get(cat)
            if cell is None:
                cell_cols.append("—")
            else:
                cell_cols.append(
                    f"{cell.severity.upper()} ({cell.finding_count}/{cell.review_status})"
                )
        writer.writerow(
            [
                row.filename,
                row.bates_range or "",
                row.privilege_status,
                row.overall_risk,
                row.finding_count,
                row.escalated_count,
                *cell_cols,
            ]
        )
    return buf.getvalue(), "text/csv", f"{slug}_risk_matrix.csv"
