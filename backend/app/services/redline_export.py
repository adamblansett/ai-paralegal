import io
import uuid
import zipfile
from datetime import UTC, datetime
from html import escape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.disclaimer import LEGAL_DISCLAIMER
from app.models.finding import ExtractedFinding
from app.models.matter import Matter

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _xml_esc(value: str) -> str:
    return escape(value, quote=True)


def _p(text: str, *, bold: bool = False, color: str | None = None, size: int = 20) -> str:
    rpr_parts = [f'<w:sz w:val="{size}"/>', f'<w:szCs w:val="{size}"/>']
    if bold:
        rpr_parts.append("<w:b/>")
    if color:
        rpr_parts.append(f'<w:color w:val="{color}"/>')
    rpr = f"<w:rPr>{''.join(rpr_parts)}</w:rPr>"
    return f'<w:p><w:r>{rpr}<w:t xml:space="preserve">{_xml_esc(text)}</w:t></w:r></w:p>'


def _track_changes_p(
    *,
    rev_id_del: int,
    rev_id_ins: int,
    deleted_text: str,
    inserted_text: str,
    timestamp_iso: str,
) -> str:
    del_block = (
        f'<w:del w:id="{rev_id_del}" w:author="AI Paralegal" w:date="{timestamp_iso}">'
        '<w:r><w:rPr><w:color w:val="B91C1C"/><w:strike/></w:rPr>'
        f'<w:delText xml:space="preserve">{_xml_esc(deleted_text)}</w:delText>'
        "</w:r></w:del>"
    )
    ins_block = (
        f'<w:ins w:id="{rev_id_ins}" w:author="AI Paralegal" w:date="{timestamp_iso}">'
        '<w:r><w:rPr><w:color w:val="15803D"/><w:u w:val="single"/></w:rPr>'
        f'<w:t xml:space="preserve">{_xml_esc(inserted_text)}</w:t>'
        "</w:r></w:ins>"
    )
    spacer = '<w:r><w:t xml:space="preserve">  </w:t></w:r>'
    return f"<w:p>{del_block}{spacer}{ins_block}</w:p>"


def _two_col_table(left_header: str, right_header: str, left_body: str, right_body: str) -> str:
    borders = (
        "<w:tblBorders>"
        '<w:top w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>'
        '<w:left w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>'
        '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>'
        '<w:right w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>'
        '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>'
        '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="CBD5E1"/>'
        "</w:tblBorders>"
    )
    cell_props = '<w:tcPr><w:tcW w:w="4680" w:type="dxa"/></w:tcPr>'
    hdr_props = (
        '<w:tcPr><w:tcW w:w="4680" w:type="dxa"/>'
        '<w:shd w:val="clear" w:color="auto" w:fill="F1F5F9"/></w:tcPr>'
    )
    row_hdr = (
        "<w:tr>"
        f"<w:tc>{hdr_props}{_p(left_header, bold=True, size=18)}</w:tc>"
        f"<w:tc>{hdr_props}{_p(right_header, bold=True, size=18)}</w:tc>"
        "</w:tr>"
    )
    row_body = (
        "<w:tr>"
        f"<w:tc>{cell_props}{_p(left_body, color='991B1B', size=18)}</w:tc>"
        f"<w:tc>{cell_props}{_p(right_body, color='166534', size=18)}</w:tc>"
        "</w:tr>"
    )
    return (
        "<w:tbl>"
        f"<w:tblPr><w:tblW w:w='9360' w:type='dxa'/>{borders}</w:tblPr>"
        "<w:tblGrid><w:gridCol w:w='4680'/><w:gridCol w:w='4680'/></w:tblGrid>"
        f"{row_hdr}{row_body}</w:tbl>"
    )


async def export_redline_docx(
    session: AsyncSession,
    *,
    matter_id: uuid.UUID,
) -> tuple[bytes, str, str]:
    """Generate a Word (.docx) redline report with side-by-side diffs and Track Changes."""
    matter = await session.get(Matter, matter_id)
    if matter is None:
        raise ValueError(f"Matter {matter_id} not found.")

    stmt = (
        select(ExtractedFinding)
        .where(
            ExtractedFinding.matter_id == matter_id,
            ExtractedFinding.finding_type.in_(["risk_flag", "missing_protection", "clause"]),
        )
        .order_by(ExtractedFinding.created_at.asc())
    )
    findings = list((await session.execute(stmt)).scalars().all())
    slug = matter.matter_number.lower().replace(" ", "_")
    timestamp_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    body_xml: list[str] = [
        _p(f"AI Paralegal Contract Redline Memorandum: {matter.title}", bold=True, size=30),
        _p(
            f"Matter: {matter.matter_number} | Client: {matter.client_name} | "
            f"Jurisdiction: {matter.jurisdiction or 'Unspecified'}",
            color="475569",
            size=20,
        ),
        _p(f"LEGAL NOTICE: {LEGAL_DISCLAIMER}", bold=True, color="92400E", size=18),
        _p(f"Total Contract Findings Included: {len(findings)}", bold=True, size=22),
    ]

    rev_counter = 1
    if not findings:
        body_xml.append(
            _p("No contract review findings have been generated for this matter yet.", size=20)
        )
    else:
        for idx, finding in enumerate(findings, start=1):
            meta = finding.metadata_json or {}
            citations = finding.citations_json or []
            cit_quote = (
                str(citations[0].get("quote", ""))
                if citations
                else "[No verbatim clause excerpt — missing protection]"
            )
            cit_loc = (
                f"{citations[0].get('document_filename', 'Document')} "
                f"p.{citations[0].get('page_number', '?')} "
                f"para {citations[0].get('paragraph_index', '?')}"
                if citations
                else "Document-level omission"
            )
            proposed = str(
                meta.get("proposed_redline")
                or finding.edited_summary
                or finding.recommended_action
                or "Attorney to draft protective clause language."
            )

            body_xml.append(
                _p(
                    f"{idx}. [{finding.severity.upper()}] {finding.title}",
                    bold=True,
                    color="1E293B",
                    size=24,
                )
            )
            body_xml.append(
                _p(
                    f"Category: {finding.category} | Grounding: {finding.grounding_score:.2f} | "
                    f"Review Status: {finding.review_status} | "
                    f"Reviewer: {finding.reviewer_name or 'Pending Attorney Review'}",
                    color="475569",
                    size=18,
                )
            )
            body_xml.append(_p(f"Analysis: {finding.edited_summary or finding.summary}", size=20))
            body_xml.append(_p(f"Source Provenance: {cit_loc}", color="334155", size=18))

            if meta.get("proposed_redline") or finding.finding_type != "clause":
                body_xml.append(
                    _two_col_table(
                        "Original Contract Language (Cited)",
                        "Proposed Playbook Fallback / Redline",
                        cit_quote,
                        proposed,
                    )
                )
                body_xml.append(_p("Word Track Changes Markup:", bold=True, size=18))
                body_xml.append(
                    _track_changes_p(
                        rev_id_del=rev_counter,
                        rev_id_ins=rev_counter + 1,
                        deleted_text=cit_quote,
                        inserted_text=proposed,
                        timestamp_iso=timestamp_iso,
                    )
                )
                rev_counter += 2
            else:
                body_xml.append(_p(f"Verified Excerpt: \"{cit_quote}\"", color="334155", size=18))

            if finding.reviewer_notes:
                body_xml.append(
                    _p(f"Attorney Notes: {finding.reviewer_notes}", color="0F172A", size=18)
                )

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W_NS}">'
        f"<w:body>{''.join(body_xml)}<w:sectPr/></w:body>"
        "</w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.'
        'wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types.encode("utf-8"))
        zf.writestr("_rels/.rels", rels_xml.encode("utf-8"))
        zf.writestr("word/document.xml", document_xml.encode("utf-8"))
    return buf.getvalue(), _DOCX_MIME, f"{slug}_redline.docx"
