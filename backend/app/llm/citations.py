import re
from difflib import SequenceMatcher

from app.core.config import Settings
from app.llm.types import RawCitation, SourceChunkContext, VerifiedCitation

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_text(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value).strip().lower()


def _best_fuzzy_score(quote: str, content: str) -> tuple[float, str | None]:
    norm_quote = _normalize_text(quote)
    norm_content = _normalize_text(content)
    if not norm_quote or not norm_content:
        return 0.0, None
    if norm_quote in norm_content:
        return 1.0, quote.strip()

    quote_words = norm_quote.split()
    content_words = norm_content.split()
    if not quote_words or not content_words:
        return 0.0, None

    window_size = min(len(content_words), max(1, len(quote_words) + 4))
    best_ratio = 0.0
    best_window = ""
    if len(content_words) <= window_size:
        ratio = SequenceMatcher(None, norm_quote, norm_content).ratio()
        return ratio, content.strip()[:280]

    step = max(1, len(quote_words) // 3)
    for start in range(0, len(content_words) - window_size + 1, step):
        candidate = " ".join(content_words[start : start + window_size])
        ratio = SequenceMatcher(None, norm_quote, candidate).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_window = candidate

    # Also check token overlap ratio for shorter phrases
    q_set = set(quote_words)
    c_set = set(content_words)
    overlap = len(q_set & c_set) / max(1, len(q_set))
    combined = max(best_ratio, overlap * 0.92 if len(q_set) >= 4 else best_ratio)
    return combined, best_window[:280] if best_window else None


def verify_citations(
    raw_citations: list[RawCitation],
    chunk_map: dict[str, SourceChunkContext],
) -> list[VerifiedCitation]:
    """Verify LLM-returned citations against authoritative SourceChunkContext records."""
    verified: list[VerifiedCitation] = []

    for raw in raw_citations:
        quote = raw.quote.strip()
        chunk = chunk_map.get(raw.chunk_id)

        # If model swapped chunk_ref (e.g. C1) or slightly drifted chunk_id, try chunk_ref lookup
        if chunk is None and raw.chunk_id:
            for item in chunk_map.values():
                if item.chunk_ref.lower() == raw.chunk_id.strip().lower():
                    chunk = item
                    break

        # If chunk_id unknown/missing, check if quote matches any supplied chunk verbatim
        if chunk is None and quote:
            norm_quote = _normalize_text(quote)
            for item in chunk_map.values():
                if norm_quote and norm_quote in _normalize_text(item.content):
                    chunk = item
                    break

        if chunk is None:
            verified.append(
                VerifiedCitation(
                    chunk_id=raw.chunk_id or "unknown",
                    quote=quote or "[missing quote]",
                    verification_status="unverified",
                    match_score=0.0,
                )
            )
            continue

        norm_quote = _normalize_text(quote)
        norm_content = _normalize_text(chunk.content)

        if norm_quote and norm_quote in norm_content:
            verified.append(
                VerifiedCitation(
                    chunk_id=chunk.chunk_id,
                    chunk_ref=chunk.chunk_ref,
                    document_id=chunk.document_id,
                    document_filename=chunk.document_filename,
                    page_number=chunk.page_number,
                    paragraph_index=chunk.paragraph_index,
                    section_heading=chunk.section_heading,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    quote=quote,
                    matched_excerpt=quote,
                    verification_status="verified_exact",
                    match_score=1.0,
                )
            )
            continue

        fuzzy_score, excerpt = _best_fuzzy_score(quote, chunk.content)
        if fuzzy_score >= 0.72:
            verified.append(
                VerifiedCitation(
                    chunk_id=chunk.chunk_id,
                    chunk_ref=chunk.chunk_ref,
                    document_id=chunk.document_id,
                    document_filename=chunk.document_filename,
                    page_number=chunk.page_number,
                    paragraph_index=chunk.paragraph_index,
                    section_heading=chunk.section_heading,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    quote=quote or chunk.content[:220],
                    matched_excerpt=excerpt,
                    verification_status="verified_fuzzy",
                    match_score=round(fuzzy_score, 3),
                )
            )
        else:
            verified.append(
                VerifiedCitation(
                    chunk_id=chunk.chunk_id,
                    chunk_ref=chunk.chunk_ref,
                    document_id=chunk.document_id,
                    document_filename=chunk.document_filename,
                    page_number=chunk.page_number,
                    paragraph_index=chunk.paragraph_index,
                    section_heading=chunk.section_heading,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    quote=quote or "[unverified quote]",
                    matched_excerpt=excerpt,
                    verification_status="unverified",
                    match_score=round(fuzzy_score, 3),
                )
            )

    return verified


def compute_grounding_score(
    citations: list[VerifiedCitation], *, allow_empty: bool = False
) -> float:
    """Compute aggregate citation grounding score in [0.0, 1.0]."""
    if not citations:
        return 1.0 if allow_empty else 0.0

    weights = {
        "verified_exact": 1.0,
        "verified_fuzzy": 0.75,
        "unverified": 0.0,
    }
    total = sum(weights[c.verification_status] for c in citations)
    return round(total / len(citations), 3)


def evaluate_escalation(
    *,
    confidence: float,
    grounding_score: float,
    severity: str,
    unverified_count: int,
    settings: Settings,
    missing_protection: bool = False,
) -> tuple[bool, str | None]:
    """Determine whether human attorney escalation is required and explain why."""
    reasons: list[str] = []

    if confidence < settings.llm_confidence_threshold:
        reasons.append(
            f"confidence {confidence:.2f} below threshold {settings.llm_confidence_threshold:.2f}"
        )
    if not missing_protection and grounding_score < settings.llm_grounding_threshold:
        reasons.append(
            f"grounding score {grounding_score:.2f} below threshold "
            f"{settings.llm_grounding_threshold:.2f}"
        )
    if unverified_count > 0:
        reasons.append(f"{unverified_count} unverified citation(s) detected")
    if settings.llm_escalate_high_risk and severity in {"high", "critical"}:
        reasons.append(f"{severity}-severity legal risk requires licensed attorney review")

    if not reasons:
        return False, None
    return True, "; ".join(reasons)
