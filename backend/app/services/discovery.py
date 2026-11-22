import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.disclaimer import LEGAL_DISCLAIMER
from app.llm.citations import compute_grounding_score, evaluate_escalation, verify_citations
from app.llm.embeddings import EmbeddingService
from app.llm.prompts import (
    PROMPT_VERSION,
    build_discovery_synthesis_prompt,
    build_research_qa_prompt,
)
from app.llm.router import ModelRouter
from app.llm.types import (
    DiscoverySynthesisLLMOutput,
    LLMRequest,
    ResearchAnswerLLMOutput,
    VerifiedCitation,
)
from app.models.finding import ExtractedFinding
from app.models.review_task import ReviewTask
from app.services.audit import record_audit_log
from app.services.events import event_bus
from app.services.retrieval import (
    chunks_to_source_contexts,
    hybrid_search,
    load_matter_chunks,
)


async def run_discovery_synthesis(
    session: AsyncSession,
    router: ModelRouter,
    *,
    matter_id: uuid.UUID,
    task_id: uuid.UUID,
    document_ids: list[uuid.UUID] | None = None,
    focus_topics: list[str] | None = None,
    provider: str | None = None,
    model: str | None = None,
    settings: Settings | None = None,
) -> ReviewTask:
    """Synthesize chronology, entities, contradictions, and omissions across matter documents."""
    cfg = settings or get_settings()
    task = await session.get(ReviewTask, task_id)
    if task is None:
        raise ValueError(f"ReviewTask {task_id} not found.")

    task.status = "running"
    task.started_at = datetime.now(UTC)
    task.error_message = None
    await session.commit()

    try:
        chunks = await load_matter_chunks(
            session,
            matter_id=matter_id,
            document_ids=document_ids,
            limit=100,
        )
        if not chunks:
            raise ValueError(
                "No indexed document chunks found for discovery synthesis. "
                "Upload and index documents first."
            )

        template_name, system_prompt, user_prompt = build_discovery_synthesis_prompt(
            chunks, focus_topics=focus_topics
        )
        llm_request = LLMRequest(
            task_kind="discovery_synthesis",
            prompt_template=template_name,
            prompt_version=PROMPT_VERSION,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            context_chunks=chunks,
            metadata={"focus_topics": focus_topics or []},
        )
        llm_response = await router.generate_structured(
            llm_request,
            DiscoverySynthesisLLMOutput,
            provider=provider,
            model=model,
        )
        output = llm_response.data
        chunk_map = {c.chunk_id: c for c in chunks}

        created_finding_ids: list[str] = []
        grounding_scores: list[float] = []
        any_escalated = False

        def _primary_doc(citations: list[VerifiedCitation]) -> uuid.UUID | None:
            for c in citations:
                if c.document_id:
                    return uuid.UUID(c.document_id)
            return document_ids[0] if document_ids else None

        async def _save_finding(
            *,
            finding_type: str,
            category: str,
            title: str,
            summary: str,
            severity: str,
            confidence: float,
            raw_citations: list[Any],
            recommended_action: str | None = None,
            extra_meta: dict[str, Any] | None = None,
        ) -> None:
            nonlocal any_escalated
            verified = verify_citations(raw_citations, chunk_map)
            g_score = compute_grounding_score(verified)
            unverified_cnt = sum(1 for v in verified if v.verification_status == "unverified")
            escalate, esc_reason = evaluate_escalation(
                confidence=confidence,
                grounding_score=g_score,
                severity=severity,
                unverified_count=unverified_cnt,
                settings=cfg,
            )
            if escalate:
                any_escalated = True
            grounding_scores.append(g_score)

            finding = ExtractedFinding(
                matter_id=matter_id,
                document_id=_primary_doc(verified),
                review_task_id=task.id,
                finding_type=finding_type,
                category=category,
                title=title[:255],
                summary=summary,
                severity=severity,
                confidence=confidence,
                grounding_score=g_score,
                requires_escalation=escalate,
                escalation_reason=esc_reason,
                recommended_action=recommended_action,
                citations_json=[v.model_dump() for v in verified],
                metadata_json={
                    **(extra_meta or {}),
                    "provider": llm_response.provider,
                    "model_name": llm_response.model_name,
                },
                review_status="pending_review",
            )
            session.add(finding)
            await session.flush()
            created_finding_ids.append(str(finding.id))

        timeline_payload: list[dict[str, Any]] = []
        for ev in output.timeline_events:
            verified_ev_cits = verify_citations(ev.citations, chunk_map)
            timeline_payload.append(
                {
                    "date_label": ev.date_label,
                    "title": ev.title,
                    "description": ev.description,
                    "citations": [v.model_dump() for v in verified_ev_cits],
                }
            )
            await _save_finding(
                finding_type="timeline_event",
                category="chronology",
                title=f"{ev.date_label}: {ev.title}",
                summary=ev.description,
                severity=ev.severity,
                confidence=ev.confidence,
                raw_citations=ev.citations,
                extra_meta={"date_label": ev.date_label},
            )

        entities_payload: list[dict[str, Any]] = []
        for ent in output.entities:
            verified_ent_cits = verify_citations(ent.citations, chunk_map)
            entities_payload.append(
                {
                    "name": ent.name,
                    "entity_type": ent.entity_type,
                    "role": ent.role,
                    "citations": [v.model_dump() for v in verified_ent_cits],
                }
            )
            await _save_finding(
                finding_type="entity",
                category=ent.entity_type,
                title=f"{ent.name} ({ent.entity_type})",
                summary=ent.role,
                severity="info",
                confidence=0.88,
                raw_citations=ent.citations,
                extra_meta={"entity_name": ent.name, "entity_type": ent.entity_type},
            )

        for con in output.contradictions:
            await _save_finding(
                finding_type="contradiction",
                category="cross_document_conflict",
                title=con.title,
                summary=con.description,
                severity=con.severity,
                confidence=con.confidence,
                raw_citations=con.citations,
                recommended_action=con.recommended_action,
            )

        for om in output.omissions:
            await _save_finding(
                finding_type="omission",
                category="missing_evidence",
                title=om.title,
                summary=om.description,
                severity=om.severity,
                confidence=om.confidence,
                raw_citations=om.citations,
                recommended_action=om.recommended_action,
            )

        avg_grounding = (
            round(sum(grounding_scores) / len(grounding_scores), 3) if grounding_scores else 0.0
        )

        task.status = "completed"
        task.resolved_provider = llm_response.provider
        task.resolved_model = llm_response.model_name
        task.completed_at = datetime.now(UTC)
        task.result_json = {
            "executive_summary": output.executive_summary,
            "timeline_events": timeline_payload,
            "entities": entities_payload,
            "key_facts": output.key_facts,
            "contradiction_count": len(output.contradictions),
            "omission_count": len(output.omissions),
            "finding_ids": created_finding_ids,
            "uncertainties": output.uncertainties,
            "average_grounding_score": avg_grounding,
            "disclaimer": LEGAL_DISCLAIMER,
        }

        await record_audit_log(
            session,
            matter_id=matter_id,
            review_task_id=task.id,
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
                "timeline_count": len(output.timeline_events),
                "entity_count": len(output.entities),
                "contradiction_count": len(output.contradictions),
                "omission_count": len(output.omissions),
                "fallback_used": llm_response.fallback_used,
            },
            token_usage=llm_response.token_usage.model_dump(),
            latency_ms=llm_response.latency_ms,
            grounding_score=avg_grounding,
            escalated=any_escalated,
            previous_state="running",
            new_state="completed",
            notes=output.executive_summary,
        )

        await session.commit()
        await session.refresh(task)
        event_bus.publish(
            matter_id,
            "task_completed",
            {
                "task_id": str(task.id),
                "task_type": "discovery_synthesis",
                "status": "completed",
                "finding_count": len(created_finding_ids),
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
                "task_type": "discovery_synthesis",
                "status": "failed",
                "error_message": str(exc),
            },
        )
        raise


async def run_research_query(
    session: AsyncSession,
    router: ModelRouter,
    embedding_service: EmbeddingService,
    *,
    matter_id: uuid.UUID,
    question: str,
    document_ids: list[uuid.UUID] | None = None,
    top_k: int = 8,
    provider: str | None = None,
    model: str | None = None,
    save_to_queue: bool = False,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Run hybrid search + citation-grounded legal research Q&A."""
    cfg = settings or get_settings()
    retrieved = await hybrid_search(
        session,
        embedding_service,
        matter_id=matter_id,
        query=question,
        document_ids=document_ids,
        top_k=top_k,
    )
    source_contexts = chunks_to_source_contexts(retrieved)
    chunk_map = {c.chunk_id: c for c in source_contexts}

    template_name, system_prompt, user_prompt = build_research_qa_prompt(question, source_contexts)
    llm_request = LLMRequest(
        task_kind="qa_research",
        prompt_template=template_name,
        prompt_version=PROMPT_VERSION,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        context_chunks=source_contexts,
        metadata={"question": question},
    )
    llm_response = await router.generate_structured(
        llm_request,
        ResearchAnswerLLMOutput,
        provider=provider,
        model=model,
    )
    output = llm_response.data
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

    finding_id: uuid.UUID | None = None
    task = ReviewTask(
        matter_id=matter_id,
        task_type="qa_research",
        status="completed",
        requested_provider=provider,
        requested_model=model,
        resolved_provider=llm_response.provider,
        resolved_model=llm_response.model_name,
        input_params_json={"question": question, "top_k": top_k},
        result_json={
            "answer": output.answer,
            "grounding_score": g_score,
            "requires_escalation": escalate,
            "disclaimer": LEGAL_DISCLAIMER,
        },
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    session.add(task)
    await session.flush()

    if save_to_queue:
        primary_doc_id: uuid.UUID | None = None
        for cit in verified:
            if cit.document_id:
                primary_doc_id = uuid.UUID(cit.document_id)
                break
        finding = ExtractedFinding(
            matter_id=matter_id,
            document_id=primary_doc_id,
            review_task_id=task.id,
            finding_type="research_answer",
            category="legal_research",
            title=question[:255],
            summary=output.answer,
            severity="medium" if escalate else "info",
            confidence=output.confidence,
            grounding_score=g_score,
            requires_escalation=escalate,
            escalation_reason=esc_reason,
            citations_json=[v.model_dump() for v in verified],
            metadata_json={
                "key_points": output.key_points,
                "uncertainties": output.uncertainties,
                "missing_context": output.missing_context,
                "disclaimer": LEGAL_DISCLAIMER,
            },
            review_status="pending_review",
        )
        session.add(finding)
        await session.flush()
        finding_id = finding.id

    await record_audit_log(
        session,
        matter_id=matter_id,
        review_task_id=task.id,
        finding_id=finding_id,
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
            for c in source_contexts
        ],
        response_metadata={
            "question": question,
            "uncertainties": output.uncertainties,
            "missing_context": output.missing_context,
            "fallback_used": llm_response.fallback_used,
        },
        token_usage=llm_response.token_usage.model_dump(),
        latency_ms=llm_response.latency_ms,
        grounding_score=g_score,
        escalated=escalate,
        new_state="completed",
        notes=f"Research query answered with {len(verified)} citation(s).",
    )
    await session.commit()

    return {
        "task_id": task.id,
        "finding_id": finding_id,
        "question": question,
        "answer": output.answer,
        "key_points": output.key_points,
        "uncertainties": output.uncertainties,
        "missing_context": output.missing_context,
        "confidence": output.confidence,
        "grounding_score": g_score,
        "requires_escalation": escalate,
        "escalation_reason": esc_reason,
        "citations": verified,
        "provider": llm_response.provider,
        "model_name": llm_response.model_name,
        "disclaimer": LEGAL_DISCLAIMER,
    }
