import difflib
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentChunk
from app.schemas.legal_ops import (
    ClauseDiffHunk,
    DocumentComparisonResponse,
    RiskRegressionFlag,
)
from app.services.audit import record_audit_log

_DOLLAR_RE = re.compile(r"\$([0-9][0-9,]*(?:\.[0-9]{1,2})?)")
_NOTICE_DAYS_RE = re.compile(
    r"(\d{1,3})\s*(?:calendar|business)?\s*days?(?:'|\s+prior)?\s*(?:written\s+)?notice",
    re.IGNORECASE,
)
_CURE_DAYS_RE = re.compile(
    r"(\d{1,3})\s*(?:calendar|business)?\s*days?\s*(?:to\s+cure|cure\s+period)",
    re.IGNORECASE,
)
_GOV_LAW_RE = re.compile(
    r"(?:laws\s+of\s+(?:the\s+)?(?:State\s+of\s+)?|governed\s+by\s+(?:the\s+)?laws\s+of\s+)"
    r"([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)",
)


def _extract_dollars(text: str) -> list[float]:
    values: list[float] = []
    for match in _DOLLAR_RE.findall(text):
        try:
            values.append(float(match.replace(",", "")))
        except ValueError:
            continue
    return values


def _detect_risk_regressions(base_text: str, rev_text: str) -> list[RiskRegressionFlag]:
    flags: list[RiskRegressionFlag] = []
    base_lower = base_text.lower()
    rev_lower = rev_text.lower()

    # 1. Liability cap lowered in revised draft
    base_caps = [
        v
        for line in base_text.splitlines()
        if any(k in line.lower() for k in ("liab", "cap", "aggregate"))
        for v in _extract_dollars(line)
    ]
    rev_caps = [
        v
        for line in rev_text.splitlines()
        if any(k in line.lower() for k in ("liab", "cap", "aggregate"))
        for v in _extract_dollars(line)
    ]
    if base_caps and rev_caps and min(rev_caps) < min(base_caps):
        flags.append(
            RiskRegressionFlag(
                severity="high",
                category="limitation_of_liability",
                title="Liability cap reduced in revised version",
                detail=(
                    f"Minimum liability cap decreased from ${min(base_caps):,.0f} "
                    f"to ${min(rev_caps):,.0f}."
                ),
            )
        )

    # 2. Mutual indemnification narrowed or removed
    if "mutual indemnif" in base_lower and "mutual indemnif" not in rev_lower:
        flags.append(
            RiskRegressionFlag(
                severity="high",
                category="indemnification",
                title="Mutual indemnification protection removed or narrowed",
                detail="Base draft contained mutual indemnification absent in the revised draft.",
            )
        )
    elif "shall indemnify" in base_lower and "solely" in rev_lower and "solely" not in base_lower:
        flags.append(
            RiskRegressionFlag(
                severity="high",
                category="indemnification",
                title="One-sided indemnification obligation introduced",
                detail="Revised draft appears to shift indemnity obligations to a single party.",
            )
        )

    # 3. Auto-renewal added or termination/renewal notice period shortened
    if (
        "auto-renew" not in base_lower
        and "automatically renew" not in base_lower
        and ("auto-renew" in rev_lower or "automatically renew" in rev_lower)
    ):
        flags.append(
            RiskRegressionFlag(
                severity="medium",
                category="renewal_notice",
                title="Auto-renewal clause added",
                detail="Revised draft introduces automatic contract renewal.",
            )
        )

    base_notice = [int(n) for n in _NOTICE_DAYS_RE.findall(base_text)]
    rev_notice = [int(n) for n in _NOTICE_DAYS_RE.findall(rev_text)]
    if base_notice and rev_notice and min(rev_notice) > max(base_notice):
        flags.append(
            RiskRegressionFlag(
                severity="medium",
                category="renewal_notice",
                title="Notice window burden increased",
                detail=(
                    f"Required prior notice window increased from {max(base_notice)} days "
                    f"to {min(rev_notice)} days."
                ),
            )
        )

    # 4. Cure period shortened
    base_cure = [int(n) for n in _CURE_DAYS_RE.findall(base_text)]
    rev_cure = [int(n) for n in _CURE_DAYS_RE.findall(rev_text)]
    if base_cure and rev_cure and min(rev_cure) < max(base_cure):
        flags.append(
            RiskRegressionFlag(
                severity="medium",
                category="termination",
                title="Breach cure period shortened",
                detail=(
                    f"Cure window shortened from {max(base_cure)} days to {min(rev_cure)} days."
                ),
            )
        )

    # 5. Governing law / jurisdiction shift
    base_law = {m.strip() for m in _GOV_LAW_RE.findall(base_text)}
    rev_law = {m.strip() for m in _GOV_LAW_RE.findall(rev_text)}
    if base_law and rev_law and base_law != rev_law:
        flags.append(
            RiskRegressionFlag(
                severity="medium",
                category="governing_law",
                title="Governing law jurisdiction changed",
                detail=(
                    f"Governing law changed from {', '.join(sorted(base_law))} "
                    f"to {', '.join(sorted(rev_law))}."
                ),
            )
        )

    # 6. Unilateral assignment without consent
    if "without consent" in rev_lower and "without consent" not in base_lower:
        flags.append(
            RiskRegressionFlag(
                severity="medium",
                category="assignment",
                title="Assignment without consent introduced",
                detail="Revised text permits assignment without prior counterparty consent.",
            )
        )

    return flags


def _unified_diff(section: str, base_txt: str, rev_txt: str) -> str:
    diff_lines = difflib.unified_diff(
        base_txt.splitlines(),
        rev_txt.splitlines(),
        fromfile=f"v1 ({section})",
        tofile=f"v2 ({section})",
        lineterm="",
    )
    return "\n".join(diff_lines)


async def compare_matter_documents(
    session: AsyncSession,
    *,
    matter_id: uuid.UUID,
    base_document_id: uuid.UUID,
    revised_document_id: uuid.UUID,
    actor_id: str,
) -> DocumentComparisonResponse:
    """Compare two matter document versions by chunk/section and detect legal risk regressions."""
    if base_document_id == revised_document_id:
        raise ValueError("Select two distinct documents to compare versions.")

    base_doc = await session.get(Document, base_document_id)
    rev_doc = await session.get(Document, revised_document_id)
    if base_doc is None or base_doc.matter_id != matter_id:
        raise FileNotFoundError("Base document was not found in this matter.")
    if rev_doc is None or rev_doc.matter_id != matter_id:
        raise FileNotFoundError("Revised document was not found in this matter.")

    base_chunks = list(
        (
            await session.execute(
                select(DocumentChunk)
                .where(DocumentChunk.document_id == base_doc.id)
                .order_by(DocumentChunk.chunk_index.asc())
            )
        )
        .scalars()
        .all()
    )
    rev_chunks = list(
        (
            await session.execute(
                select(DocumentChunk)
                .where(DocumentChunk.document_id == rev_doc.id)
                .order_by(DocumentChunk.chunk_index.asc())
            )
        )
        .scalars()
        .all()
    )

    base_texts = [c.content.strip() for c in base_chunks if c.content.strip()]
    rev_texts = [c.content.strip() for c in rev_chunks if c.content.strip()]

    matcher = difflib.SequenceMatcher(None, base_texts, rev_texts)
    hunks: list[ClauseDiffHunk] = []
    added_count = 0
    removed_count = 0
    modified_count = 0
    unchanged_count = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                c = base_chunks[i1 + offset]
                heading = c.section_heading or f"Section {c.chunk_index + 1}"
                hunks.append(
                    ClauseDiffHunk(
                        section_heading=heading,
                        change_type="unchanged",
                        base_text=c.content,
                        revised_text=rev_chunks[j1 + offset].content,
                        unified_diff="",
                    )
                )
                unchanged_count += 1
        elif tag == "insert":
            for idx in range(j1, j2):
                c = rev_chunks[idx]
                heading = c.section_heading or f"Added Clause {idx + 1}"
                hunks.append(
                    ClauseDiffHunk(
                        section_heading=heading,
                        change_type="added",
                        base_text=None,
                        revised_text=c.content,
                        unified_diff=_unified_diff(heading, "", c.content),
                        risk_impact="New clause added in revised version",
                    )
                )
                added_count += 1
        elif tag == "delete":
            for idx in range(i1, i2):
                c = base_chunks[idx]
                heading = c.section_heading or f"Removed Clause {idx + 1}"
                hunks.append(
                    ClauseDiffHunk(
                        section_heading=heading,
                        change_type="removed",
                        base_text=c.content,
                        revised_text=None,
                        unified_diff=_unified_diff(heading, c.content, ""),
                        risk_impact="Clause removed from revised version",
                    )
                )
                removed_count += 1
        elif tag == "replace":
            pair_count = max(i2 - i1, j2 - j1)
            for offset in range(pair_count):
                b_chunk = base_chunks[i1 + offset] if (i1 + offset) < i2 else None
                r_chunk = rev_chunks[j1 + offset] if (j1 + offset) < j2 else None
                heading = (
                    (r_chunk.section_heading if r_chunk else None)
                    or (b_chunk.section_heading if b_chunk else None)
                    or f"Clause {offset + 1}"
                )
                b_txt = b_chunk.content if b_chunk else ""
                r_txt = r_chunk.content if r_chunk else ""
                change_type: str = (
                    "modified"
                    if b_chunk and r_chunk
                    else ("added" if r_chunk else "removed")
                )
                if change_type == "modified":
                    modified_count += 1
                elif change_type == "added":
                    added_count += 1
                else:
                    removed_count += 1
                hunks.append(
                    ClauseDiffHunk(
                        section_heading=heading,
                        change_type=change_type,  # type: ignore[arg-type]
                        base_text=b_txt or None,
                        revised_text=r_txt or None,
                        unified_diff=_unified_diff(heading, b_txt, r_txt),
                        risk_impact="Redline modification requires attorney review",
                    )
                )

    full_base = "\n".join(base_texts)
    full_rev = "\n".join(rev_texts)
    regressions = _detect_risk_regressions(full_base, full_rev)

    md_lines = [
        f"# Contract Redline Comparison: {base_doc.filename} -> {rev_doc.filename}",
        "",
        f"- **Added:** {added_count} | **Removed:** {removed_count} | "
        f"**Modified:** {modified_count} | **Unchanged:** {unchanged_count}",
        f"- **Risk Regressions Flagged:** {len(regressions)}",
    ]
    if regressions:
        md_lines.extend(["", "## Risk Regression Alerts"])
        for reg in regressions:
            md_lines.append(f"- **[{reg.severity.upper()}] {reg.title}**: {reg.detail}")

    summary_markdown = "\n".join(md_lines)

    await record_audit_log(
        session,
        matter_id=matter_id,
        document_id=rev_doc.id,
        action="document_comparison",
        actor_type="human",
        actor_id=actor_id,
        new_state="completed",
        notes=(
            f"Compared '{base_doc.filename}' vs '{rev_doc.filename}': "
            f"{modified_count} modified, {added_count} added, {removed_count} removed, "
            f"{len(regressions)} risk regression(s)."
        ),
        response_metadata={
            "base_document_id": str(base_doc.id),
            "revised_document_id": str(rev_doc.id),
            "risk_regressions": len(regressions),
        },
    )
    await session.commit()

    return DocumentComparisonResponse(
        matter_id=matter_id,
        base_document_id=base_doc.id,
        base_filename=base_doc.filename,
        revised_document_id=rev_doc.id,
        revised_filename=rev_doc.filename,
        total_hunks=len(hunks),
        added_count=added_count,
        removed_count=removed_count,
        modified_count=modified_count,
        unchanged_count=unchanged_count,
        hunks=hunks,
        risk_regressions=regressions,
        summary_markdown=summary_markdown,
    )
