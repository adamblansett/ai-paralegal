import re
from dataclasses import dataclass

from pydantic import BaseModel, Field

PRIVILEGE_STATUSES: tuple[str, ...] = (
    "none",
    "attorney_client",
    "work_product",
    "confidential",
    "privileged_and_confidential",
)

CLOUD_RESTRICTED_PRIVILEGES: frozenset[str] = frozenset(
    {
        "attorney_client",
        "work_product",
        "privileged_and_confidential",
    }
)

_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_EIN_RE = re.compile(r"\b\d{2}-\d{7}\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b")
_BANK_ACCOUNT_RE = re.compile(
    r"(?i)\b(?:account(?:\s*(?:no\.?|number|#))?|routing(?:\s*(?:no\.?|number|#))?|iban)"
    r"\s*[:#]?\s*([A-Z0-9-]{6,22})\b"
)
_CARD_CANDIDATE_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")

_PRIVILEGE_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "privileged_and_confidential",
        re.compile(r"(?i)\bprivileged\s*(?:&|and)\s*confidential\b"),
        "Marked 'Privileged and Confidential'",
    ),
    (
        "attorney_client",
        re.compile(r"(?i)\battorney[- ]client\s+privileg(?:e|ed)\b"),
        "Contains Attorney-Client Privilege header/marker",
    ),
    (
        "work_product",
        re.compile(
            r"(?i)\b(?:attorney\s+)?work[- ]product\b|"
            r"\bprepared\s+in\s+anticipation\s+of\s+litigation\b|"
            r"\bmental\s+impressions\s+of\s+counsel\b"
        ),
        "Contains Attorney Work Product designation",
    ),
    (
        "attorney_client",
        re.compile(
            r"(?i)\bfor\s+(?:outside\s+|internal\s+)?counsel\s+review\s+only\b|"
            r"\blegal\s+advice\s+sought\b"
        ),
        "Contains counsel-only legal advice marker",
    ),
    (
        "confidential",
        re.compile(r"(?i)\bstrictly\s+confidential\b|\battorneys['’]?\s+eyes\s+only\b|\bAEO\b"),
        "Marked Strictly Confidential / Attorneys' Eyes Only",
    ),
]


def _is_luhn_valid(digits: str) -> bool:
    if not (13 <= len(digits) <= 19) or not digits.isdigit():
        return False
    total = 0
    reverse_digits = digits[::-1]
    for idx, ch in enumerate(reverse_digits):
        n = int(ch)
        if idx % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


@dataclass(slots=True, frozen=True)
class _PIISpan:
    start: int
    end: int
    pii_type: str


class PIIScanResult(BaseModel):
    total_count: int = 0
    counts_by_type: dict[str, int] = Field(default_factory=dict)
    sample_types: list[str] = Field(default_factory=list)


class PrivilegeDetection(BaseModel):
    suggested_status: str = "none"
    confidence: float = 0.0
    matched_markers: list[str] = Field(default_factory=list)
    reason: str | None = None


def _collect_pii_spans(text: str) -> list[_PIISpan]:
    if not text:
        return []

    raw_spans: list[_PIISpan] = []
    # Order reflects match priority when spans overlap (e.g., SSN before phone/card)
    for m in _SSN_RE.finditer(text):
        raw_spans.append(_PIISpan(m.start(), m.end(), "ssn"))
    for m in _EMAIL_RE.finditer(text):
        raw_spans.append(_PIISpan(m.start(), m.end(), "email"))
    for m in _BANK_ACCOUNT_RE.finditer(text):
        raw_spans.append(_PIISpan(m.start(1), m.end(1), "bank_account"))
    for m in _CARD_CANDIDATE_RE.finditer(text):
        digits = re.sub(r"[^0-9]", "", m.group(0))
        if _is_luhn_valid(digits):
            raw_spans.append(_PIISpan(m.start(), m.end(), "credit_card"))
    for m in _EIN_RE.finditer(text):
        raw_spans.append(_PIISpan(m.start(), m.end(), "ein"))
    for m in _PHONE_RE.finditer(text):
        raw_spans.append(_PIISpan(m.start(), m.end(), "phone"))

    # Deduplicate overlapping spans greedily preserving earlier-priority / earlier start matches
    accepted: list[_PIISpan] = []
    for candidate in raw_spans:
        if candidate.start >= candidate.end:
            continue
        overlaps = any(
            not (candidate.end <= existing.start or candidate.start >= existing.end)
            for existing in accepted
        )
        if not overlaps:
            accepted.append(candidate)

    accepted.sort(key=lambda sp: sp.start)
    return accepted


def scan_text_for_pii(text: str) -> PIIScanResult:
    """Detect structured PII patterns in extracted legal document text."""
    spans = _collect_pii_spans(text)
    counts: dict[str, int] = {}
    for sp in spans:
        counts[sp.pii_type] = counts.get(sp.pii_type, 0) + 1
    return PIIScanResult(
        total_count=len(spans),
        counts_by_type=counts,
        sample_types=sorted(counts.keys()),
    )


def redact_pii_for_cloud(text: str) -> tuple[str, dict[str, int]]:
    """Redact detected PII spans before outbound cloud LLM transmission."""
    spans = _collect_pii_spans(text)
    if not spans:
        return text, {}

    out_parts: list[str] = []
    counts: dict[str, int] = {}
    cursor = 0
    for sp in spans:
        if sp.start < cursor:
            continue
        counts[sp.pii_type] = counts.get(sp.pii_type, 0) + 1
        idx = counts[sp.pii_type]
        out_parts.append(text[cursor : sp.start])
        out_parts.append(f"[REDACTED_{sp.pii_type.upper()}_{idx}]")
        cursor = sp.end
    out_parts.append(text[cursor:])
    return "".join(out_parts), counts


def detect_privilege_indicators(text: str, filename: str = "") -> PrivilegeDetection:
    """Heuristically identify privilege/confidentiality markings during ingestion."""
    haystack = f"{filename}\n{text[:15000]}"
    matched_statuses: list[str] = []
    markers: list[str] = []
    reasons: list[str] = []

    for status_name, pattern, label in _PRIVILEGE_PATTERNS:
        m = pattern.search(haystack)
        if m:
            matched_statuses.append(status_name)
            markers.append(m.group(0).strip())
            if label not in reasons:
                reasons.append(label)

    if not matched_statuses:
        return PrivilegeDetection(suggested_status="none", confidence=0.0)

    # Prioritize stronger privilege classifications
    priority = [
        "privileged_and_confidential",
        "attorney_client",
        "work_product",
        "confidential",
    ]
    chosen = "confidential"
    for p in priority:
        if p in matched_statuses:
            chosen = p
            break

    return PrivilegeDetection(
        suggested_status=chosen,
        confidence=0.92 if len(markers) >= 2 else 0.85,
        matched_markers=markers[:5],
        reason="; ".join(reasons[:3]),
    )
