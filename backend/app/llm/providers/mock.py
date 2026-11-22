import re
from collections import defaultdict
from typing import Literal, TypeVar

from pydantic import BaseModel

from app.llm.providers.base import LLMProvider
from app.llm.types import (
    ClauseFindingOutput,
    ContractReviewLLMOutput,
    ContradictionOutput,
    DiscoverySynthesisLLMOutput,
    DraftOutputLLMOutput,
    EntityOutput,
    LLMRequest,
    OmissionOutput,
    RawCitation,
    ResearchAnswerLLMOutput,
    SeverityLevel,
    SourceChunkContext,
    TimelineEventOutput,
    TokenUsage,
)

T = TypeVar("T", bound=BaseModel)

_DATE_RE = re.compile(
    r"\b(?:"
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2},\s+\d{4}"
    r"|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}"
    r"|\d{4}-\d{2}-\d{2}"
    r")\b"
)
_DOLLAR_RE = re.compile(r"\$[0-9][0-9,]*(?:\.[0-9]{2})?")
_WORD_RE = re.compile(r"[a-z0-9]{3,}")


def _pick_excerpt(content: str, keywords: list[str], max_len: int = 240) -> str:
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", content) if s.strip()]
    if not sentences:
        return content.strip()[:max_len]
    best_sentence = sentences[0]
    best_score = 0
    for sentence in sentences:
        lower = sentence.lower()
        score = sum(1 for kw in keywords if kw in lower)
        if score > best_score:
            best_score = score
            best_sentence = sentence
    return best_sentence[:max_len]


def _score_chunk(chunk: SourceChunkContext, keywords: list[str]) -> int:
    text = f"{chunk.section_heading or ''} {chunk.content}".lower()
    return sum(text.count(kw) for kw in keywords)


class MockLLMProvider(LLMProvider):
    """Deterministic, document-aware offline provider for tests and air-gapped dev."""

    name = "mock"

    async def generate_structured(
        self,
        request: LLMRequest,
        schema_cls: type[T],
        *,
        model: str | None = None,
    ) -> tuple[T, str, str, TokenUsage]:
        resolved_model = model or "mock-legal-deterministic-v1"
        chunks = request.context_chunks

        result: BaseModel
        if request.task_kind == "contract_review":
            result = self._run_contract_review(chunks, request)
        elif request.task_kind == "discovery_synthesis":
            result = self._run_discovery_synthesis(chunks)
        elif request.task_kind == "qa_research":
            question = str(request.metadata.get("question", request.user_prompt))
            result = self._run_qa_research(question, chunks)
        else:
            draft_type = str(request.metadata.get("draft_type", "internal_memo"))
            instructions = str(request.metadata.get("instructions", "Draft matter summary"))
            result = self._run_drafting(draft_type, instructions, chunks)

        validated = schema_cls.model_validate(result.model_dump())
        raw_text = validated.model_dump_json(indent=2)
        prompt_toks = max(1, len(request.user_prompt.split()))
        comp_toks = max(1, len(raw_text.split()))
        usage = TokenUsage(
            prompt_tokens=prompt_toks,
            completion_tokens=comp_toks,
            total_tokens=prompt_toks + comp_toks,
        )
        return validated, resolved_model, raw_text, usage

    def _run_contract_review(
        self, chunks: list[SourceChunkContext], request: LLMRequest
    ) -> ContractReviewLLMOutput:
        requested_clauses: list[str] = list(
            request.metadata.get(
                "clause_types",
                [
                    "indemnification",
                    "limitation_of_liability",
                    "termination",
                    "ip_assignment",
                    "confidentiality",
                    "governing_law",
                    "renewal_notice",
                ],
            )
        )
        playbook_rules_raw = request.metadata.get("playbook_rules")
        playbook_rules: dict[str, dict[str, object]] = (
            playbook_rules_raw if isinstance(playbook_rules_raw, dict) else {}
        )
        clause_specs: dict[str, tuple[str, list[str]]] = {
            "indemnification": ("Indemnification Clause", ["indemnif", "hold harmless", "defend"]),
            "limitation_of_liability": (
                "Limitation of Liability",
                [
                    "limitation of liability",
                    "aggregate liability",
                    "liable",
                    "cap",
                    "consequential",
                ],
            ),
            "termination": (
                "Termination & Cure Rights",
                ["terminat", "cure period", "convenience", "suspend"],
            ),
            "ip_assignment": (
                "Intellectual Property Assignment",
                ["intellectual property", "assigns and transfers", "background ip", "derivative"],
            ),
            "confidentiality": (
                "Confidentiality & Data Sharing",
                ["confidential", "perpetuity", "sub-processor", "subpoena"],
            ),
            "governing_law": (
                "Governing Law & Dispute Resolution",
                ["governing law", "construed in accordance", "arbitration", "jurisdiction"],
            ),
            "renewal_notice": (
                "Term & Renewal Notice Window",
                ["renew", "initial term", "notice of non-renewal", "days prior"],
            ),
        }

        findings: list[ClauseFindingOutput] = []
        missing_protections: list[str] = []
        highest_severity: SeverityLevel = "low"
        severity_rank = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

        for clause_key in requested_clauses:
            title, keywords = clause_specs.get(
                clause_key, (clause_key.replace("_", " ").title(), [clause_key.replace("_", " ")])
            )
            ranked = sorted(chunks, key=lambda c: _score_chunk(c, keywords), reverse=True)
            best = ranked[0] if ranked and _score_chunk(ranked[0], keywords) > 0 else None

            if best is None:
                missing_protections.append(
                    f"No explicit '{title}' provision located in reviewed text."
                )
                findings.append(
                    ClauseFindingOutput(
                        finding_type="missing_protection",
                        category=clause_key,
                        title=f"Missing Standard Clause: {title}",
                        summary=(
                            f"Reviewed chunks did not contain an identifiable {title} provision."
                        ),
                        severity="high",
                        confidence=0.84,
                        is_non_standard=True,
                        recommended_action=f"Draft and insert balanced {title} protections.",
                        citations=[
                            RawCitation(
                                chunk_id=chunks[0].chunk_id,
                                quote=_pick_excerpt(chunks[0].content, ["agreement"]),
                            )
                        ]
                        if chunks
                        else [],
                    )
                )
                if severity_rank["high"] > severity_rank[highest_severity]:
                    highest_severity = "high"
                continue

            lower = best.content.lower()
            excerpt = _pick_excerpt(best.content, keywords)
            risk_notes: list[str] = []
            severity: SeverityLevel = "low"
            is_non_standard = False

            if clause_key == "indemnification" and (
                "uncapped" in lower
                or "no indemnification" in lower
                or "regardless of whether" in lower
            ):
                severity = "critical"
                is_non_standard = True
                risk_notes.append(
                    "One-sided, uncapped client indemnity covering vendor-contributed fault "
                    "with zero reciprocal IP indemnity."
                )
            elif clause_key == "limitation_of_liability" and (
                "one (1) month" in lower or "no carve-out" in lower or "$20,000" in lower
            ):
                severity = "critical"
                is_non_standard = True
                risk_notes.append(
                    "Super-low liability cap (1 month / $20,000) without carve-outs for "
                    "gross negligence, fraud, or confidentiality breaches."
                )
            elif clause_key == "ip_assignment" and (
                "background ip" in lower
                or "without any license back" in lower
                or "pre-existing" in lower
            ):
                severity = "high"
                is_non_standard = True
                risk_notes.append(
                    "Overbroad IP assignment transfers Client background IP and "
                    "pre-existing tools with no license-back carve-out."
                )
            elif clause_key == "confidentiality" and (
                "in perpetuity" in lower
                or "without carve-out" in lower
                or "sole discretion" in lower
            ):
                severity = "high"
                is_non_standard = True
                risk_notes.append(
                    "Perpetual confidentiality obligation lacks standard public-domain / "
                    "compelled-disclosure carve-outs while allowing vendor sub-processor sharing."
                )
            elif clause_key == "termination" and (
                "without refund" in lower or "no cure period" in lower or "10 days" in lower
            ):
                severity = "high"
                is_non_standard = True
                risk_notes.append(
                    "Asymmetric termination: Vendor may terminate for convenience on 10 days' "
                    "notice without refund; Client lacks cure or convenience rights."
                )
            elif clause_key == "renewal_notice" and (
                "automatically renew" in lower
                or "90 days" in lower
                or "twenty-four (24) month" in lower
            ):
                severity = "medium"
                is_non_standard = True
                risk_notes.append(
                    "Long 24-month auto-renewal term requiring 90 days' advance written "
                    "non-renewal notice."
                )
            else:
                risk_notes.append(f"Extracted {title} provision appears structurally standard.")

            if severity_rank[severity] > severity_rank[highest_severity]:
                highest_severity = severity

            finding_type: Literal["clause", "risk_flag", "missing_protection"] = (
                "risk_flag" if is_non_standard else "clause"
            )
            rule_info = playbook_rules.get(clause_key, {})
            fallback_text = (
                str(rule_info.get("fallback_position"))
                if is_non_standard and rule_info.get("fallback_position")
                else None
            )
            findings.append(
                ClauseFindingOutput(
                    finding_type=finding_type,
                    category=clause_key,
                    title=f"{title}" + (" — Non-Standard Risk Flag" if is_non_standard else ""),
                    summary=" ".join(risk_notes),
                    severity=severity,
                    confidence=0.91 if is_non_standard else 0.88,
                    is_non_standard=is_non_standard,
                    recommended_action=(
                        "Redline clause to restore mutual commercial protections before execution."
                        if is_non_standard
                        else "Confirm business alignment during routine sign-off."
                    ),
                    proposed_redline=fallback_text,
                    citations=[RawCitation(chunk_id=best.chunk_id, quote=excerpt)],
                )
            )

        # Check cross-cutting omitted protections
        joined_text = "\n".join(c.content.lower() for c in chunks)
        protective_checks = [
            (
                "data_security_breach",
                "Missing Data Security & Breach Notification Clause",
                ["breach notification", "security incident", "iso 27001", "soc 2"],
                "No binding security standards or breach notification SLA appears "
                "in the reviewed agreement.",
            ),
            (
                "insurance_coverage",
                "Missing Cyber & E&O Insurance Covenant",
                ["insurance", "errors and omissions", "cyber liability"],
                "Contract contains no minimum cyber/errors-and-omissions insurance "
                "coverage requirement.",
            ),
        ]
        for cat, title, kws, desc in protective_checks:
            if not any(kw in joined_text for kw in kws):
                missing_protections.append(title)
                anchor = chunks[-1] if chunks else None
                cat_rule = playbook_rules.get(cat, {})
                cat_fallback = (
                    str(cat_rule.get("fallback_position"))
                    if cat_rule.get("fallback_position")
                    else None
                )
                findings.append(
                    ClauseFindingOutput(
                        finding_type="missing_protection",
                        category=cat,
                        title=title,
                        summary=desc,
                        severity="high",
                        confidence=0.86,
                        is_non_standard=True,
                        recommended_action=(
                            f"Add explicit {title.replace('Missing ', '')} addendum."
                        ),
                        proposed_redline=cat_fallback,
                        citations=[
                            RawCitation(
                                chunk_id=anchor.chunk_id,
                                quote=_pick_excerpt(
                                    anchor.content, ["entire understanding", "services"]
                                ),
                            )
                        ]
                        if anchor
                        else [],
                    )
                )
                if severity_rank["high"] > severity_rank[highest_severity]:
                    highest_severity = "high"

        uncertainties = [
            "Referenced Statements of Work, Exhibit B SLA schedules, or Data Processing Addenda "
            "were not part of the reviewed contract text if omitted from upload."
        ]

        return ContractReviewLLMOutput(
            executive_summary=(
                f"Reviewed {len(chunks)} source chunk(s) across "
                f"{len(requested_clauses)} clause categories. "
                f"Identified {sum(1 for f in findings if f.is_non_standard)} "
                "non-standard risk or missing-protection findings."
            ),
            overall_risk_level=highest_severity,
            findings=findings,
            missing_protections=missing_protections,
            uncertainties=uncertainties,
        )

    def _run_discovery_synthesis(
        self, chunks: list[SourceChunkContext]
    ) -> DiscoverySynthesisLLMOutput:
        timeline: list[TimelineEventOutput] = []
        entities: list[EntityOutput] = []
        seen_entities: set[str] = set()
        key_facts: list[str] = []
        contradictions: list[ContradictionOutput] = []
        omissions: list[OmissionOutput] = []

        by_doc: dict[str, list[SourceChunkContext]] = defaultdict(list)
        for chunk in chunks:
            by_doc[chunk.document_filename].append(chunk)

            # Timeline extraction from real dated sentences
            for sentence in re.split(r"(?<=[.!?])\s+|\n+", chunk.content):
                cleaned = sentence.strip()
                if not cleaned:
                    continue
                match = _DATE_RE.search(cleaned)
                if match and len(timeline) < 12:
                    timeline.append(
                        TimelineEventOutput(
                            date_label=match.group(0),
                            title=f"Event on {match.group(0)} ({chunk.document_filename})",
                            description=cleaned[:260],
                            severity="info",
                            confidence=0.90,
                            citations=[RawCitation(chunk_id=chunk.chunk_id, quote=cleaned[:220])],
                        )
                    )

            # Entity extraction (Parties, People, Monetary amounts)
            for org_match in re.finditer(
                r"\b([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3}\s+(?:Corp\.|Inc\.|LLP|LLC|Group))\b",
                chunk.content,
            ):
                name = org_match.group(1).strip()
                if name not in seen_entities:
                    seen_entities.add(name)
                    entities.append(
                        EntityOutput(
                            name=name,
                            entity_type="organization",
                            role="Contracting party or referenced entity",
                            citations=[
                                RawCitation(
                                    chunk_id=chunk.chunk_id,
                                    quote=_pick_excerpt(chunk.content, [name.lower().split()[0]]),
                                )
                            ],
                        )
                    )

            for amt_match in _DOLLAR_RE.finditer(chunk.content):
                amt = amt_match.group(0)
                if amt not in seen_entities:
                    seen_entities.add(amt)
                    entities.append(
                        EntityOutput(
                            name=amt,
                            entity_type="monetary_amount",
                            role="Referenced fee, cap, or disputed balance",
                            citations=[
                                RawCitation(
                                    chunk_id=chunk.chunk_id,
                                    quote=_pick_excerpt(chunk.content, [amt.lower()]),
                                )
                            ],
                        )
                    )

            # Omission signals (missing attachments/exhibits/DPA)
            lower = chunk.content.lower()
            if any(
                kw in lower
                for kw in [
                    "exhibit b",
                    "still needs to circulate",
                    "did not include",
                    "missing exhibit",
                    "data processing addendum",
                ]
            ):
                excerpt = _pick_excerpt(
                    chunk.content,
                    ["exhibit b", "did not include", "still needs to circulate", "addendum"],
                )
                omissions.append(
                    OmissionOutput(
                        title=f"Missing Referenced Exhibit or Schedule ({chunk.document_filename})",
                        description=(
                            "Record references Exhibit B SLA schedule or Data Processing "
                            "Addendum that has not been produced or attached."
                        ),
                        severity="high",
                        confidence=0.89,
                        recommended_action=(
                            "Issue targeted supplemental document request for Exhibit B and DPA."
                        ),
                        citations=[RawCitation(chunk_id=chunk.chunk_id, quote=excerpt)],
                    )
                )

        # Cross-document contradiction detection (fees & liability cap discrepancies)
        chunks_185 = [c for c in chunks if "$185,000" in c.content]
        chunks_240 = [c for c in chunks if "$240,000" in c.content]
        if chunks_185 and chunks_240:
            c1 = chunks_185[0]
            c2 = chunks_240[0]
            contradictions.append(
                ContradictionOutput(
                    title="Fee Discrepancy: $185,000 Email Confirmation vs. $240,000 MSA Fee",
                    description=(
                        f"{c1.document_filename} confirms an annual subscription fee of "
                        f"$185,000, whereas {c2.document_filename} states an annual "
                        "subscription fee of $240,000."
                    ),
                    severity="critical",
                    confidence=0.94,
                    recommended_action=(
                        "Reconcile signed order form/email commitment against integration "
                        "clause Section 10.2."
                    ),
                    citations=[
                        RawCitation(
                            chunk_id=c1.chunk_id,
                            quote=_pick_excerpt(c1.content, ["$185,000"]),
                        ),
                        RawCitation(
                            chunk_id=c2.chunk_id,
                            quote=_pick_excerpt(c2.content, ["$240,000"]),
                        ),
                    ],
                )
            )

        chunks_cap_12 = [c for c in chunks if "12 months of fees" in c.content.lower()]
        chunks_cap_1 = [
            c
            for c in chunks
            if "one (1) month" in c.content.lower() or "$20,000" in c.content.lower()
        ]
        if chunks_cap_12 and chunks_cap_1:
            c1 = chunks_cap_12[0]
            c2 = chunks_cap_1[0]
            contradictions.append(
                ContradictionOutput(
                    title="Liability Cap Contradiction: 12 Months vs. 1 Month ($20,000)",
                    description=(
                        f"{c1.document_filename} states a 12-month fee liability cap ($185,000), "
                        f"contradicting the 1-month ($20,000) cap in {c2.document_filename}."
                    ),
                    severity="high",
                    confidence=0.92,
                    recommended_action=(
                        "Confirm negotiation history and whether a side letter or "
                        "amendment exists."
                    ),
                    citations=[
                        RawCitation(
                            chunk_id=c1.chunk_id,
                            quote=_pick_excerpt(c1.content, ["12 months of fees"]),
                        ),
                        RawCitation(
                            chunk_id=c2.chunk_id,
                            quote=_pick_excerpt(c2.content, ["one (1) month", "$20,000"]),
                        ),
                    ],
                )
            )

        if timeline:
            key_facts.append(
                f"Extracted {len(timeline)} dated chronology event(s) "
                f"across {len(by_doc)} document(s)."
            )
        if contradictions:
            key_facts.append(
                f"Flagged {len(contradictions)} cross-document contradiction(s) "
                "requiring attorney reconciliation."
            )
        if omissions:
            key_facts.append(f"Identified {len(omissions)} missing exhibit/addendum reference(s).")

        return DiscoverySynthesisLLMOutput(
            executive_summary=(
                f"Synthesized {len(chunks)} chunks across {len(by_doc)} matter document(s): "
                f"{len(timeline)} timeline events, {len(entities)} entities, "
                f"{len(contradictions)} contradictions, and {len(omissions)} omissions."
            ),
            timeline_events=timeline,
            entities=entities,
            key_facts=key_facts,
            contradictions=contradictions,
            omissions=omissions,
            uncertainties=[
                "Chronology completeness depends on all relevant correspondence and executed "
                "schedules being uploaded to the matter."
            ],
        )

    def _run_qa_research(
        self, question: str, chunks: list[SourceChunkContext]
    ) -> ResearchAnswerLLMOutput:
        if not chunks:
            return ResearchAnswerLLMOutput(
                answer=(
                    "No indexed matter chunks were available to answer this question. "
                    "Please upload and index relevant documents first."
                ),
                confidence=0.25,
                key_points=[],
                uncertainties=["No evidentiary chunks retrieved for the query."],
                missing_context=["Indexed matter documents covering the requested subject."],
                citations=[],
            )

        q_tokens = [tok for tok in _WORD_RE.findall(question.lower()) if len(tok) >= 3]
        scored = sorted(
            chunks,
            key=lambda c: sum(c.content.lower().count(t) for t in q_tokens),
            reverse=True,
        )
        top_chunks = scored[:3]
        citations: list[RawCitation] = []
        key_points: list[str] = []

        for chunk in top_chunks:
            excerpt = _pick_excerpt(chunk.content, q_tokens or ["agreement"], max_len=240)
            citations.append(RawCitation(chunk_id=chunk.chunk_id, quote=excerpt))
            loc = f"{chunk.document_filename}"
            if chunk.section_heading:
                loc += f" ({chunk.section_heading})"
            key_points.append(f'[{chunk.chunk_ref}] {loc}: "{excerpt}"')

        top_hit_score = (
            sum(top_chunks[0].content.lower().count(t) for t in q_tokens) if q_tokens else 1
        )
        confidence = 0.89 if top_hit_score >= 2 else 0.68

        uncertainties: list[str] = []
        missing_context: list[str] = []
        if top_hit_score < 2:
            uncertainties.append(
                "Query terms had limited direct overlap with indexed chunks; "
                "answer reflects closest available excerpts."
            )
        joined = "\n".join(c.content.lower() for c in top_chunks)
        if "exhibit b" in joined or "addendum" in joined:
            missing_context.append(
                "Referenced Exhibit B SLA schedule or Data Processing Addendum may alter "
                "final obligations if executed separately."
            )
        if len({c.document_id for c in chunks}) == 1:
            missing_context.append(
                "Only one source document was retrieved; corroborating emails or "
                "amendments were not checked."
            )

        answer_lines = [
            f"Based strictly on the {len(top_chunks)} retrieved matter excerpt(s) "
            f'addressing "{question.strip()}":',
            "",
            *key_points,
        ]
        return ResearchAnswerLLMOutput(
            answer="\n".join(answer_lines),
            confidence=confidence,
            key_points=key_points,
            uncertainties=uncertainties,
            missing_context=missing_context,
            citations=citations,
        )

    def _run_drafting(
        self, draft_type: str, instructions: str, chunks: list[SourceChunkContext]
    ) -> DraftOutputLLMOutput:
        selected = chunks[:4]
        citations: list[RawCitation] = []
        evidence_bullets: list[str] = []

        for chunk in selected:
            excerpt = _pick_excerpt(
                chunk.content, ["shall", "fee", "liability", "notice", "exhibit"], max_len=220
            )
            citations.append(RawCitation(chunk_id=chunk.chunk_id, quote=excerpt))
            evidence_bullets.append(
                f"- **[{chunk.chunk_ref}] {chunk.document_filename}** "
                f'(p.{chunk.page_number or 1}, ¶{chunk.paragraph_index or 1}): "{excerpt}"'
            )

        heading_map = {
            "internal_memo": "Internal Legal Operations Memorandum (Draft)",
            "clause_comparison": "Contract Clause & Commercial Alignment Comparison (Draft)",
            "discovery_requests": "Draft Supplemental Requests for Production (RFP)",
            "discovery_responses": "Draft Preliminary Discovery Responses & Objections",
            "review_checklist": "Matter Document & Risk Review Checklist",
        }
        title = heading_map.get(draft_type, f"Legal Draft: {draft_type.replace('_', ' ').title()}")

        sections = [
            f"# {title}",
            "",
            f"**Drafting Scope / Instructions:** {instructions.strip()}",
            "",
            "## Grounded Source Record",
            *(
                evidence_bullets
                or ["- No source chunks supplied. [ATTORNEY VERIFICATION REQUIRED]"]
            ),
            "",
            "## Preliminary Analysis / Work Product",
            (
                "1. Verify governing contract terms against pre-execution commercial "
                "correspondence and any integration clause (`[ATTORNEY VERIFICATION REQUIRED]`)."
            ),
            (
                "2. Confirm whether omitted schedules (e.g., Exhibit B SLA, Data Processing "
                "Addendum) were executed separately (`[ATTORNEY VERIFICATION REQUIRED]`)."
            ),
            "",
            "## Open Questions Requiring Licensed Attorney Sign-Off",
            (
                "- Confirm final litigation/negotiation posture and "
                "jurisdiction-specific enforceability."
            ),
        ]

        return DraftOutputLLMOutput(
            title=title,
            draft_markdown="\n".join(sections),
            confidence=0.86 if selected else 0.50,
            uncertainties=[
                "Draft reflects only uploaded matter sources and requires attorney validation "
                "of omitted exhibits and governing law."
            ],
            missing_context=["Any unuploaded signed amendments, SOWs, or oral negotiation notes."],
            citations=citations,
        )
