import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import DocumentChunk
from app.models.finding import ExtractedFinding
from app.models.matter import Matter
from app.schemas.legal_ops import ConflictCheckResponse, ConflictMatch

_SUFFIX_RE = re.compile(
    r"\b(?:inc|incorporated|llc|llp|lp|ltd|limited|corp|corporation|co|company|plc|gmbh)\b\.?",
    re.IGNORECASE,
)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]+")


def _normalize_party(name: str) -> str:
    cleaned = _SUFFIX_RE.sub("", name.lower())
    cleaned = _NON_ALNUM_RE.sub(" ", cleaned)
    return " ".join(cleaned.split())


def _is_party_match(query_norm: str, candidate_norm: str) -> tuple[bool, float]:
    if not query_norm or not candidate_norm:
        return False, 0.0
    if query_norm == candidate_norm:
        return True, 0.98
    if len(query_norm) >= 4 and (query_norm in candidate_norm or candidate_norm in query_norm):
        return True, 0.85
    q_tokens = {t for t in query_norm.split() if len(t) >= 3}
    c_tokens = {t for t in candidate_norm.split() if len(t) >= 3}
    if q_tokens and c_tokens:
        overlap = q_tokens & c_tokens
        ratio = len(overlap) / max(len(q_tokens), len(c_tokens))
        if ratio >= 0.5:
            return True, round(0.65 + 0.25 * ratio, 2)
    return False, 0.0


async def run_conflict_check(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID | None,
    client_name: str,
    counterparties: list[str],
    matter_title: str | None = None,
    exclude_matter_id: uuid.UUID | None = None,
) -> ConflictCheckResponse:
    """Screen prospective clients and counterparties against tenant matters, entities & docs."""
    search_terms = [
        t.strip() for t in [client_name, *counterparties] if t and len(t.strip()) >= 2
    ]
    if matter_title:
        for seg in re.split(r"\bv\.?\b|\bvs\.?\b|—|-", matter_title, flags=re.IGNORECASE):
            seg_clean = seg.strip()
            if len(seg_clean) >= 3 and seg_clean not in search_terms:
                search_terms.append(seg_clean)

    matter_stmt = select(Matter)
    if organization_id is not None:
        matter_stmt = matter_stmt.where(
            (Matter.organization_id == organization_id) | (Matter.organization_id.is_(None))
        )
    if exclude_matter_id is not None:
        matter_stmt = matter_stmt.where(Matter.id != exclude_matter_id)

    matters = list((await session.execute(matter_stmt)).scalars().all())
    matter_by_id = {m.id: m for m in matters}
    matches: list[ConflictMatch] = []
    seen_keys: set[tuple[uuid.UUID, str, str]] = set()

    def _add_match(match: ConflictMatch) -> None:
        key = (match.matter_id, match.matched_party.lower(), match.match_source)
        if key not in seen_keys:
            seen_keys.add(key)
            matches.append(match)

    # 1. Screen against existing tenant matter client names and titles
    for matter in matters:
        norm_client = _normalize_party(matter.client_name)
        norm_title = _normalize_party(matter.title)
        for term in search_terms:
            norm_term = _normalize_party(term)
            hit_client, conf_c = _is_party_match(norm_term, norm_client)
            if hit_client:
                is_counterparty = term != client_name
                _add_match(
                    ConflictMatch(
                        matter_id=matter.id,
                        matter_number=matter.matter_number,
                        matter_title=matter.title,
                        client_name=matter.client_name,
                        matched_party=term,
                        match_source="client_name",
                        confidence=conf_c,
                        severity="high" if is_counterparty or conf_c >= 0.9 else "medium",
                        snippet=f"Existing matter client: {matter.client_name}",
                    )
                )
            hit_title, conf_t = _is_party_match(norm_term, norm_title)
            if hit_title and not hit_client:
                _add_match(
                    ConflictMatch(
                        matter_id=matter.id,
                        matter_number=matter.matter_number,
                        matter_title=matter.title,
                        client_name=matter.client_name,
                        matched_party=term,
                        match_source="matter_title",
                        confidence=round(conf_t * 0.9, 2),
                        severity="medium",
                        snippet=f"Matched matter title: {matter.title}",
                    )
                )

    if matters:
        matter_ids = list(matter_by_id.keys())
        # 2. Screen extracted discovery/contract entity findings
        ent_rows = (
            await session.execute(
                select(ExtractedFinding).where(
                    ExtractedFinding.matter_id.in_(matter_ids),
                    ExtractedFinding.finding_type == "entity",
                )
            )
        ).scalars().all()
        for ent in ent_rows:
            ent_matter = matter_by_id.get(ent.matter_id)
            if ent_matter is None:
                continue
            norm_ent = _normalize_party(ent.title)
            for term in search_terms:
                hit, conf = _is_party_match(_normalize_party(term), norm_ent)
                if hit:
                    _add_match(
                        ConflictMatch(
                            matter_id=ent_matter.id,
                            matter_number=ent_matter.matter_number,
                            matter_title=ent_matter.title,
                            client_name=ent_matter.client_name,
                            matched_party=term,
                            match_source="discovered_entity",
                            confidence=conf,
                            severity="high" if conf >= 0.85 else "medium",
                            snippet=f"Extracted entity '{ent.title}' ({ent.summary[:120]})",
                        )
                    )

        # 3. Scan indexed chunks for counterparty mentions
        chunk_rows = (
            await session.execute(
                select(DocumentChunk).where(DocumentChunk.matter_id.in_(matter_ids)).limit(400)
            )
        ).scalars().all()
        for chunk in chunk_rows:
            chunk_matter = matter_by_id.get(chunk.matter_id)
            if chunk_matter is None:
                continue
            lower_content = chunk.content.lower()
            for term in search_terms:
                norm_term = _normalize_party(term)
                if len(norm_term) < 4:
                    continue
                pos = lower_content.find(norm_term)
                if pos != -1:
                    start = max(0, pos - 40)
                    end = min(len(chunk.content), pos + len(norm_term) + 80)
                    _add_match(
                        ConflictMatch(
                            matter_id=chunk_matter.id,
                            matter_number=chunk_matter.matter_number,
                            matter_title=chunk_matter.title,
                            client_name=chunk_matter.client_name,
                            matched_party=term,
                            match_source="document_text",
                            confidence=0.75,
                            severity="low" if term == client_name else "medium",
                            snippet=chunk.content[start:end].strip(),
                        )
                    )

    matches.sort(key=lambda m: m.confidence, reverse=True)
    has_conflicts = len(matches) > 0
    if any(m.severity == "high" for m in matches):
        risk_level: str = "high"
        recommended_action = (
            "Potential direct adverse-party conflict detected. Require Managing Partner clearance "
            "and establish an Ethical Wall before staffing conflicted attorneys."
        )
    elif any(m.severity == "medium" for m in matches):
        risk_level = "medium"
        recommended_action = (
            "Related party or prior representation reference found. Verify scope and clear "
            "conflicts with Intake Partner."
        )
    elif matches:
        risk_level = "low"
        recommended_action = "Minor textual reference found; confirm non-adverse relationship."
    else:
        risk_level = "none"
        recommended_action = "No tenant conflicts detected across existing matters or records."

    return ConflictCheckResponse(
        query_client=client_name,
        query_counterparties=counterparties,
        has_conflicts=has_conflicts,
        risk_level=risk_level,  # type: ignore[arg-type]
        recommended_action=recommended_action,
        matches=matches[:25],
    )
