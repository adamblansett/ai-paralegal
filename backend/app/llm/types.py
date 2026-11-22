from typing import Any, Literal

from pydantic import BaseModel, Field

SeverityLevel = Literal["info", "low", "medium", "high", "critical"]
VerificationStatus = Literal["verified_exact", "verified_fuzzy", "unverified"]
TaskKind = Literal["contract_review", "discovery_synthesis", "qa_research", "drafting"]


class SourceChunkContext(BaseModel):
    """Authoritative source chunk passed to LLM prompts and citation verification."""

    chunk_ref: str
    chunk_id: str
    document_id: str
    document_filename: str
    document_type: str = "general"
    chunk_index: int
    page_number: int | None = None
    paragraph_index: int | None = None
    section_heading: str | None = None
    char_start: int | None = None
    char_end: int | None = None
    privilege_status: str = "none"
    bates_number: str | None = None
    content: str


class RawCitation(BaseModel):
    """Citation returned by an LLM prior to verification."""

    chunk_id: str = ""
    quote: str = ""
    rationale: str | None = None


class VerifiedCitation(BaseModel):
    """Citation verified against authoritative database chunks."""

    chunk_id: str
    chunk_ref: str | None = None
    document_id: str | None = None
    document_filename: str | None = None
    page_number: int | None = None
    paragraph_index: int | None = None
    section_heading: str | None = None
    char_start: int | None = None
    char_end: int | None = None
    quote: str
    matched_excerpt: str | None = None
    verification_status: VerificationStatus
    match_score: float = Field(default=0.0, ge=0.0, le=1.0)


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LLMRequest(BaseModel):
    """Normalized request passed across LLM providers."""

    task_kind: TaskKind
    prompt_template: str
    prompt_version: str
    system_prompt: str
    user_prompt: str
    context_chunks: list[SourceChunkContext] = Field(default_factory=list)
    temperature: float = 0.1
    max_tokens: int = 2048
    metadata: dict[str, Any] = Field(default_factory=dict)


class LLMResponse[T: BaseModel](BaseModel):
    """Normalized structured output returned by ModelRouter."""

    provider: str
    model_name: str
    data: T
    raw_text: str
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    latency_ms: int = 0
    fallback_used: bool = False
    fallback_errors: list[str] = Field(default_factory=list)
    redaction_summary: dict[str, int] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Structured LLM Output Contracts
# ---------------------------------------------------------------------------


class ClauseFindingOutput(BaseModel):
    finding_type: Literal["clause", "risk_flag", "missing_protection"]
    category: str
    title: str
    summary: str
    severity: SeverityLevel = "medium"
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    is_non_standard: bool = False
    recommended_action: str | None = None
    proposed_redline: str | None = None
    citations: list[RawCitation] = Field(default_factory=list)


class ContractReviewLLMOutput(BaseModel):
    executive_summary: str
    overall_risk_level: SeverityLevel = "medium"
    findings: list[ClauseFindingOutput] = Field(default_factory=list)
    missing_protections: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)


class TimelineEventOutput(BaseModel):
    date_label: str
    title: str
    description: str
    severity: SeverityLevel = "info"
    confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    citations: list[RawCitation] = Field(default_factory=list)


class EntityOutput(BaseModel):
    name: str
    entity_type: str
    role: str
    citations: list[RawCitation] = Field(default_factory=list)


class ContradictionOutput(BaseModel):
    title: str
    description: str
    severity: SeverityLevel = "high"
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    recommended_action: str | None = None
    citations: list[RawCitation] = Field(default_factory=list)


class OmissionOutput(BaseModel):
    title: str
    description: str
    severity: SeverityLevel = "medium"
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    recommended_action: str | None = None
    citations: list[RawCitation] = Field(default_factory=list)


class DiscoverySynthesisLLMOutput(BaseModel):
    executive_summary: str
    timeline_events: list[TimelineEventOutput] = Field(default_factory=list)
    entities: list[EntityOutput] = Field(default_factory=list)
    key_facts: list[str] = Field(default_factory=list)
    contradictions: list[ContradictionOutput] = Field(default_factory=list)
    omissions: list[OmissionOutput] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)


class ResearchAnswerLLMOutput(BaseModel):
    answer: str
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    key_points: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    missing_context: list[str] = Field(default_factory=list)
    citations: list[RawCitation] = Field(default_factory=list)


class DraftOutputLLMOutput(BaseModel):
    title: str
    draft_markdown: str
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    uncertainties: list[str] = Field(default_factory=list)
    missing_context: list[str] = Field(default_factory=list)
    citations: list[RawCitation] = Field(default_factory=list)
