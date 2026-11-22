import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.playbook import Playbook

DEFAULT_SAAS_MSA_RULES: list[dict[str, Any]] = [
    {
        "clause_type": "indemnification",
        "standard_position": (
            "Mutual third-party indemnification capped at liability limits for IP infringement, "
            "bodily injury, and gross negligence; no client indemnity for vendor fault."
        ),
        "fallback_position": (
            "Each party shall defend and indemnify the other from third-party claims to the "
            "extent directly caused by the indemnifying party's gross negligence, willful "
            "misconduct, or breach of applicable law. Vendor shall additionally defend Client "
            "against claims that the Services infringe third-party intellectual property."
        ),
        "red_flag_patterns": ["uncapped", "no indemnification", "regardless of whether"],
        "required": True,
        "severity_if_deviated": "critical",
    },
    {
        "clause_type": "limitation_of_liability",
        "standard_position": (
            "Mutual aggregate liability cap of at least 12 months of fees paid or payable, "
            "with carve-outs for IP indemnity, confidentiality breaches, and gross negligence."
        ),
        "fallback_position": (
            "EXCEPT FOR CONFIDENTIALITY BREACHES, INDEMNIFICATION OBLIGATIONS, GROSS NEGLIGENCE, "
            "OR WILLFUL MISCONDUCT, EACH PARTY'S AGGREGATE LIABILITY SHALL NOT EXCEED THE TOTAL "
            "FEES PAID OR PAYABLE BY CLIENT IN THE TWELVE (12) MONTHS PRECEDING THE CLAIM."
        ),
        "red_flag_patterns": ["one (1) month", "$20,000", "no carve-out"],
        "required": True,
        "severity_if_deviated": "critical",
    },
    {
        "clause_type": "ip_assignment",
        "standard_position": (
            "Client retains all ownership of Client Data, background IP, and pre-existing tools; "
            "Vendor receives only a limited term license to host Services."
        ),
        "fallback_position": (
            "Client retains all right, title, and interest in and to Client Data, Client "
            "background intellectual property, pre-existing tools, and proprietary templates. "
            "No assignment of Client pre-existing IP is granted under this Agreement."
        ),
        "red_flag_patterns": ["background ip", "without any license back", "pre-existing"],
        "required": True,
        "severity_if_deviated": "high",
    },
    {
        "clause_type": "confidentiality",
        "standard_position": (
            "Mutual 3-to-5 year survival with public-domain, prior-knowledge, independent "
            "development, and legally compelled disclosure carve-outs."
        ),
        "fallback_position": (
            "Confidentiality obligations shall survive for five (5) years following expiration "
            "and exclude information that is publicly available, independently developed, or "
            "disclosed pursuant to court order upon prompt prior written notice. Vendor may "
            "engage sub-processors only under written confidentiality terms at least as protective."
        ),
        "red_flag_patterns": ["in perpetuity", "without carve-out", "sole discretion"],
        "required": True,
        "severity_if_deviated": "high",
    },
    {
        "clause_type": "termination",
        "standard_position": (
            "Mutual 30-day written cure period for material breach, pro-rata refund of prepaid "
            "unused fees upon vendor breach or convenience termination."
        ),
        "fallback_position": (
            "Either party may terminate for material breach uncured thirty (30) days after "
            "written notice. Vendor may terminate for convenience only upon ninety (90) days' "
            "written notice accompanied by a pro-rata refund of all prepaid, unused fees."
        ),
        "red_flag_patterns": ["without refund", "no cure period", "10 days"],
        "required": True,
        "severity_if_deviated": "high",
    },
    {
        "clause_type": "renewal_notice",
        "standard_position": (
            "Annual renewal cycles with a reasonable 30-to-45 day non-renewal notice window."
        ),
        "fallback_position": (
            "Following a twelve (12) month Initial Term, this Agreement shall renew for "
            "successive one (1) year periods unless either party provides written notice of "
            "non-renewal at least thirty (30) days prior to the end of the then-current term."
        ),
        "red_flag_patterns": ["twenty-four (24) month", "90 days", "automatically renew"],
        "required": True,
        "severity_if_deviated": "medium",
    },
    {
        "clause_type": "governing_law",
        "standard_position": "Clear Delaware or New York governing law and predictable venue.",
        "fallback_position": (
            "This Agreement is governed by the laws of the State of Delaware, and the parties "
            "consent to exclusive jurisdiction in state or federal courts in Wilmington, Delaware."
        ),
        "red_flag_patterns": [],
        "required": True,
        "severity_if_deviated": "medium",
    },
    {
        "clause_type": "data_security_breach",
        "standard_position": (
            "SOC 2 Type II / ISO 27001 controls and written security breach notice within 24-48h."
        ),
        "fallback_position": (
            "Vendor shall maintain SOC 2 Type II or ISO 27001 security controls and notify "
            "Client in writing within forty-eight (48) hours of confirming any Security Incident "
            "affecting Client Data, cooperating in remediation at Vendor's expense."
        ),
        "red_flag_patterns": [],
        "required": True,
        "severity_if_deviated": "high",
    },
    {
        "clause_type": "insurance_coverage",
        "standard_position": (
            "Minimum $5M cyber liability and errors & omissions coverage "
            "with certificates on request."
        ),
        "fallback_position": (
            "Vendor shall maintain Commercial General Liability ($2,000,000 per occurrence) and "
            "Cyber / Technology Errors & Omissions insurance ($5,000,000 aggregate) throughout "
            "the Term and furnish certificates of insurance upon Client's reasonable request."
        ),
        "red_flag_patterns": [],
        "required": True,
        "severity_if_deviated": "high",
    },
]


def format_playbook_notes(playbook: Playbook) -> str:
    """Render playbook rules into structured instructions for LLM contract review prompts."""
    lines = [f"Playbook: {playbook.name} ({playbook.practice_area})"]
    for rule in playbook.rules_json:
        ctype = str(rule.get("clause_type", "general"))
        std = str(rule.get("standard_position", ""))
        fb = str(rule.get("fallback_position", ""))
        lines.append(f"- {ctype}: Standard={std} | Preferred Fallback={fb}")
    return "\n".join(lines)


def get_rule_map(playbook: Playbook | None) -> dict[str, dict[str, Any]]:
    """Index playbook rules by clause_type."""
    if playbook is None:
        return {str(r["clause_type"]): r for r in DEFAULT_SAAS_MSA_RULES}
    return {
        str(rule.get("clause_type")): rule
        for rule in playbook.rules_json
        if rule.get("clause_type")
    }


async def resolve_playbook(
    session: AsyncSession, playbook_id: uuid.UUID | None = None
) -> Playbook | None:
    """Load the requested playbook or fall back to the default firm playbook."""
    if playbook_id is not None:
        return await session.get(Playbook, playbook_id)
    stmt = select(Playbook).where(Playbook.is_default.is_(True)).order_by(Playbook.created_at.asc())
    return (await session.execute(stmt)).scalars().first()
