from app.llm.citations import compute_grounding_score, evaluate_escalation, verify_citations
from app.llm.embeddings import EmbeddingService, cosine_similarity, deterministic_embed
from app.llm.json_utils import StructuredOutputError, extract_json_payload, parse_structured_output
from app.llm.prompts import (
    PROMPT_VERSION,
    STANDARD_CLAUSE_TYPES,
    build_contract_review_prompt,
    build_discovery_synthesis_prompt,
    build_drafting_prompt,
    build_research_qa_prompt,
)
from app.llm.router import ModelRouter

__all__ = [
    "EmbeddingService",
    "ModelRouter",
    "PROMPT_VERSION",
    "STANDARD_CLAUSE_TYPES",
    "StructuredOutputError",
    "build_contract_review_prompt",
    "build_discovery_synthesis_prompt",
    "build_drafting_prompt",
    "build_research_qa_prompt",
    "compute_grounding_score",
    "cosine_similarity",
    "deterministic_embed",
    "evaluate_escalation",
    "extract_json_payload",
    "parse_structured_output",
    "verify_citations",
]
