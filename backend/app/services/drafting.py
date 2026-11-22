import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.disclaimer import LEGAL_DISCLAIMER
from app.llm.citations import compute_grounding_score, evaluate_escalation, verify_citations
from app.llm.embeddings import EmbeddingService
from app.llm.prompts import PROMPT_VERSION, build_drafting_prompt
from app.llm.router import ModelRouter
from app.llm.types import DraftOutputLLMOutput, LLMRequest
from app.models.finding import ExtractedFinding
from app.models.review_task import ReviewTask
from app.services.audit import record_audit_log
from app.services.events import event_bus
from app.services.retrieval import (
    chunks_to_source_contexts,
    hybrid_search,
    load_matter_chunks,
)


async def run_draft_generation(
    session: AsyncSession,
    router: ModelRouter,
    embedding_service: EmbeddingService,
    *,
    matter_id: uuid.UUID,
    task_id: uuid.UUID,
    draft_type: str,
    instructions: str,
    document_ids: list[uuid.UUID] | None = None,
    provider: str | None = None,
    model: str | None = None,
    settings: Settings | None = None,
) -> ReviewTask:
    """Generate a citation-grounded legal draft and place it in the attorney review queue."""
    cfg = settings or get_settings()
    task = await session.get(ReviewTask, task_id)
    if task is None:
        raise ValueError(f"ReviewTask {task_id} not found.")

    task.status = "running"
    task.started_at = datetime.now(UTC)
    task.error_message = None
    await session.commit()

    try:
        retrieved = await hybrid_search(
            session,
            embedding_service,
            matter_id=matter_id,
            query=f"{draft_type.replace('_', ' ')} {instructions}",
            document_ids=document_ids,
            top_k=12,
        )
        chunks = (
            chunks_to_source_contexts(retrieved)
            if retrieved
            else await load_matter_chunks(
                session, matter_id=matter_id, document_ids=document_ids, limit=25
            )
        )
        if not chunks:
            raise ValueError(
                "No indexed document chunks available for drafting. "
                "Upload and index documents first."
            )

        template_name, system_prompt, user_prompt = build_drafting_prompt(
            draft_type, instructions, chunks
        )
        llm_request = LLMRequest(
            task_kind="drafting",
            prompt_template=template_name,
            prompt_version=PROMPT_VERSION,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            context_chunks=chunks,
            metadata={"draft_type": draft_type, "instructions": instructions},
        )
        llm_response = await router.generate_structured(
            llm_request,
            DraftOutputLLMOutput,
            provider=provider,
            model=model,
        )
        output = llm_response.data
        chunk_map = {c.chunk_id: c for c in chunks}
        verified = verify_citations(output.citations, chunk_map)
        g_score = compute_grounding_score(verified)
        unverified_cnt = sum(1 for v in verified if v.verification_status == "unverified")
        escalate, esc_reason = evaluate_escalation(
            confidence=output.confidence,
            grounding_score=g_score,
            severity="medium",
            unverified_count=unverified_cnt,
            settings=cfg,
        )

        primary_doc_id: uuid.UUID | None = None
        for cit in verified:
            if cit.document_id:
                primary_doc_id = uuid.UUID(cit.document_id)
                break
        if primary_doc_id is None and document_ids:
            primary_doc_id = document_ids[0]

        finding = ExtractedFinding(
            matter_id=matter_id,
            document_id=primary_doc_id,
            review_task_id=task.id,
            finding_type="draft_output",
            category=draft_type,
            title=output.title[:255],
            summary=output.draft_markdown,
            severity="medium" if escalate else "info",
            confidence=output.confidence,
            grounding_score=g_score,
            requires_escalation=True,
            escalation_reason=esc_reason
            or "All AI-generated legal drafts require licensed attorney review.",
            recommended_action=(
                "Attorney must verify citations, uncertainties, and jurisdiction rules "
                "prior to use."
            ),
            citations_json=[v.model_dump() for v in verified],
            metadata_json={
                "draft_type": draft_type,
                "instructions": instructions,
                "uncertainties": output.uncertainties,
                "missing_context": output.missing_context,
                "disclaimer": LEGAL_DISCLAIMER,
            },
            review_status="pending_review",
        )
        session.add(finding)
        await session.flush()

        task.status = "completed"
        task.resolved_provider = llm_response.provider
        task.resolved_model = llm_response.model_name
        task.completed_at = datetime.now(UTC)
        task.result_json = {
            "finding_id": str(finding.id),
            "draft_type": draft_type,
            "title": output.title,
            "draft_markdown": output.draft_markdown,
            "confidence": output.confidence,
            "grounding_score": g_score,
            "uncertainties": output.uncertainties,
            "missing_context": output.missing_context,
            "citations": [v.model_dump() for v in verified],
            "disclaimer": LEGAL_DISCLAIMER,
        }

        await record_audit_log(
            session,
            matter_id=matter_id,
            document_id=primary_doc_id,
            review_task_id=task.id,
            finding_id=finding.id,
            action="llm_invocation",
            actor_type="llm",
            actor_id=f"{llm_response.provider}:{llm_response.model_name}",
            provider=llm_response.provider,
            model_name=llm_response.model_name,
            prompt_template=template_name,
            prompt_version=PROMPT_VERSION,
            prompt_text=user_prompt[:8000],
            retrieved_spans=[
                {
                    "chunk_id": c.chunk_id,
                    "chunk_ref": c.chunk_ref,
                    "document_id": c.document_id,
                    "document_filename": c.document_filename,
                    "page_number": c.page_number,
                    "paragraph_index": c.paragraph_index,
                    "section_heading": c.section_heading,
                }
                for c in chunks
            ],
            response_metadata={
                "draft_type": draft_type,
                "title": output.title,
                "uncertainties": output.uncertainties,
                "missing_context": output.missing_context,
                "fallback_used": llm_response.fallback_used,
            },
            token_usage=llm_response.token_usage.model_dump(),
            latency_ms=llm_response.latency_ms,
            grounding_score=g_score,
            escalated=True,
            previous_state="running",
            new_state="completed",
            notes=f"Generated {draft_type} draft '{output.title}'.",
        )

        await session.commit()
        await session.refresh(task)
        event_bus.publish(
            matter_id,
            "task_completed",
            {
                "task_id": str(task.id),
                "task_type": "draft_generation",
                "status": "completed",
                "finding_id": str(finding.id),
            },
        )
        return task
    except Exception as exc:
        await session.rollback()
        failed_task = await session.get(ReviewTask, task_id)
        if failed_task is not None:
            failed_task.status = "failed"
            failed_task.error_message = str(exc)
            failed_task.completed_at = datetime.now(UTC)
            await session.commit()
        event_bus.publish(
            matter_id,
            "task_failed",
            {
                "task_id": str(task_id),
                "task_type": "draft_generation",
                "status": "failed",
                "error_message": str(exc),
            },
        )
        raise
