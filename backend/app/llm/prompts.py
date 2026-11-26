import json

from app.llm.types import (
    ContractReviewLLMOutput,
    DiscoverySynthesisLLMOutput,
    DraftOutputLLMOutput,
    ResearchAnswerLLMOutput,
    SourceChunkContext,
)

PROMPT_VERSION = "1.0.0"

STANDARD_CLAUSE_TYPES = [
    "indemnification",
    "limitation_of_liability",
    "termination",
    "ip_assignment",
    "confidentiality",
    "governing_law",
    "renewal_notice",
]


def format_chunks_for_prompt(chunks: list[SourceChunkContext]) -> str:
    """Format retrieved chunks with explicit IDs and provenance headers for grounding."""
    blocks: list[str] = []
    for chunk in chunks:
        loc_parts = [f"doc={chunk.document_filename}"]
        if chunk.page_number is not None:
            loc_parts.append(f"page={chunk.page_number}")
        if chunk.paragraph_index is not None:
            loc_parts.append(f"para={chunk.paragraph_index}")
        if chunk.section_heading:
            loc_parts.append(f"section={chunk.section_heading}")
        header = f"[{chunk.chunk_ref}] chunk_id={chunk.chunk_id} ({', '.join(loc_parts)})"
        blocks.append(f"{header}\n{chunk.content}")
    return "\n\n---\n\n".join(blocks)


def build_contract_review_prompt(
    chunks: list[SourceChunkContext],
    clause_types: list[str] | None = None,
    playbook_notes: str | None = None,
) -> tuple[str, str, str]:
    """Return `(template_name, system_prompt, user_prompt)` for contract/clause review."""
    targets = clause_types or STANDARD_CLAUSE_TYPES
    schema_hint = json.dumps(ContractReviewLLMOutput.model_json_schema(), indent=2)
    system_prompt = (
        "You are an AI Paralegal Contract Review Analyst assisting licensed attorneys. "
        "Analyze ONLY the supplied source chunks. Never invent clauses, page numbers, or quotes. "
        "Every finding MUST cite exact `chunk_id` values and quote verbatim substrings. "
        "Flag non-standard, one-sided, or uncapped terms and identify missing protections. "
        "Respond strictly with valid JSON matching the requested schema."
    )
    playbook_block = (
        f"\nPlaybook Instructions:\n{playbook_notes.strip()}\n" if playbook_notes else ""
    )
    user_prompt = (
        f"Target Clause Categories: {', '.join(targets)}\n"
        f"{playbook_block}\n"
        f"Required JSON Schema:\n{schema_hint}\n\n"
        f"Source Document Chunks:\n{format_chunks_for_prompt(chunks)}"
    )
    return "contract_review_v1", system_prompt, user_prompt


def build_discovery_synthesis_prompt(
    chunks: list[SourceChunkContext],
    focus_topics: list[str] | None = None,
) -> tuple[str, str, str]:
    """Return `(template_name, system_prompt, user_prompt)` for discovery synthesis."""
    schema_hint = json.dumps(DiscoverySynthesisLLMOutput.model_json_schema(), indent=2)
    topics_block = f"Focus Topics: {', '.join(focus_topics)}\n" if focus_topics else ""
    system_prompt = (
        "You are an AI Paralegal Discovery Synthesis Analyst assisting licensed attorneys. "
        "Synthesize timelines, entities, key facts, contradictions, and missing exhibits/omissions "
        "STRICTLY from the supplied source chunks. Quote verbatim and cite exact `chunk_id`s. "
        "Clearly state uncertainties whenever the record is incomplete. Output valid JSON only."
    )
    user_prompt = (
        f"{topics_block}"
        f"Required JSON Schema:\n{schema_hint}\n\n"
        f"Source Matter Chunks:\n{format_chunks_for_prompt(chunks)}"
    )
    return "discovery_synthesis_v1", system_prompt, user_prompt


def build_research_qa_prompt(
    question: str,
    chunks: list[SourceChunkContext],
) -> tuple[str, str, str]:
    """Return `(template_name, system_prompt, user_prompt)` for citation-grounded research Q&A."""
    schema_hint = json.dumps(ResearchAnswerLLMOutput.model_json_schema(), indent=2)
    system_prompt = (
        "You are an AI Paralegal Research Assistant. Answer the user's question STRICTLY from the "
        "supplied matter chunks. Every factual assertion must use verbatim quotes and `chunk_id`s. "
        "Explicitly list uncertainties or missing context. Never fabricate case law or quotes. "
        "Output valid JSON only."
    )
    user_prompt = (
        f"Question: {question.strip()}\n\n"
        f"Required JSON Schema:\n{schema_hint}\n\n"
        f"Retrieved Source Chunks:\n{format_chunks_for_prompt(chunks)}"
    )
    return "research_qa_v1", system_prompt, user_prompt


def build_research_qa_stream_prompt(
    question: str,
    chunks: list[SourceChunkContext],
) -> tuple[str, str, str]:
    """Return `(template_name, system_prompt, user_prompt)` for progressive SSE research Q&A."""
    system_prompt = (
        "You are an AI Paralegal Research Assistant. Answer the user's question concisely and "
        "STRICTLY from the supplied matter chunks. Cite source bracket references (e.g., [S1]) "
        "and quote key phrases verbatim. Explicitly note any missing context or uncertainties."
    )
    user_prompt = (
        f"Question: {question.strip()}\n\n"
        f"Retrieved Source Chunks:\n{format_chunks_for_prompt(chunks)}"
    )
    return "research_qa_stream_v1", system_prompt, user_prompt


def build_drafting_prompt(
    draft_type: str,
    instructions: str,
    chunks: list[SourceChunkContext],
) -> tuple[str, str, str]:
    """Return `(template_name, system_prompt, user_prompt)` for citation-grounded legal drafting."""
    schema_hint = json.dumps(DraftOutputLLMOutput.model_json_schema(), indent=2)
    system_prompt = (
        "You are an AI Paralegal Drafting Assistant preparing preliminary attorney work product. "
        "Draft the requested document strictly grounded in the supplied matter chunks, embedding "
        "citation tags and verbatim quotes with exact `chunk_id`s. Mark gaps with "
        "[ATTORNEY VERIFICATION REQUIRED] and populate `uncertainties` and `missing_context`. "
        "Output valid JSON only."
    )
    user_prompt = (
        f"Draft Type: {draft_type}\n"
        f"Instructions: {instructions.strip()}\n\n"
        f"Required JSON Schema:\n{schema_hint}\n\n"
        f"Matter Source Chunks:\n{format_chunks_for_prompt(chunks)}"
    )
    return "drafting_v1", system_prompt, user_prompt
