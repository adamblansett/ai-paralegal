import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.disclaimer import LEGAL_DISCLAIMER
from app.models.document import Document
from app.models.finding import ExtractedFinding
from app.models.matter import Matter


async def export_approved_feedback_dataset(
    session: AsyncSession,
    *,
    matter_id: uuid.UUID,
) -> tuple[str, str, str]:
    """Export attorney-approved findings & edits into regression evaluation test cases."""
    matter = await session.get(Matter, matter_id)
    if matter is None:
        raise ValueError(f"Matter {matter_id} not found.")

    doc_stmt = select(Document).where(Document.matter_id == matter_id)
    docs = {d.id: d for d in (await session.execute(doc_stmt)).scalars().all()}

    stmt = (
        select(ExtractedFinding)
        .where(
            ExtractedFinding.matter_id == matter_id,
            ExtractedFinding.review_status == "approved",
        )
        .order_by(ExtractedFinding.created_at.asc())
    )
    approved = list((await session.execute(stmt)).scalars().all())

    contract_cases: list[dict[str, Any]] = []
    qa_cases: list[dict[str, Any]] = []

    for finding in approved:
        doc_name = docs[finding.document_id].filename if finding.document_id in docs else None
        citations = finding.citations_json or []
        quotes = [str(c.get("quote", "")) for c in citations if c.get("quote")]
        if finding.finding_type in {"clause", "risk_flag", "missing_protection"}:
            contract_cases.append(
                {
                    "id": f"feedback-{str(finding.id)[:8]}",
                    "document_filename": doc_name,
                    "finding_type": finding.finding_type,
                    "category": finding.category,
                    "expected_severity": finding.severity,
                    "attorney_summary": finding.edited_summary or finding.summary,
                    "reviewer_notes": finding.reviewer_notes,
                    "expected_quotes": quotes,
                }
            )
        elif finding.finding_type == "research_answer":
            qa_cases.append(
                {
                    "id": f"feedback-qa-{str(finding.id)[:8]}",
                    "question": finding.title,
                    "attorney_answer": finding.edited_summary or finding.summary,
                    "expected_quotes": quotes,
                }
            )

    payload = {
        "version": "feedback-1.0",
        "matter": {
            "id": str(matter.id),
            "matter_number": matter.matter_number,
            "title": matter.title,
        },
        "disclaimer": LEGAL_DISCLAIMER,
        "approved_count": len(approved),
        "contract_feedback_cases": contract_cases,
        "qa_feedback_cases": qa_cases,
    }
    slug = matter.matter_number.lower().replace(" ", "_")
    return json.dumps(payload, indent=2), "application/json", f"{slug}_eval_feedback.json"
