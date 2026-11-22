import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.disclaimer import LEGAL_DISCLAIMER
from app.llm.citations import compute_grounding_score, evaluate_escalation, verify_citations
from app.llm.prompts import (
    PROMPT_VERSION,
    STANDARD_CLAUSE_TYPES,
    build_contract_review_prompt,
)
from app.llm.router import ModelRouter
from app.llm.types import ContractReviewLLMOutput, LLMRequest
from app.models.finding import ExtractedFinding
from app.models.review_task import ReviewTask
from app.services.audit import record_audit_log
from app.services.playbooks import format_playbook_notes, get_rule_map, resolve_playbook
from app.services.retrieval import load_matter_chunks


async def run_contract_review(
    session: AsyncSession,
    router: ModelRouter,
    *,
    matter_id: uuid.UUID,
    task_id: uuid.UUID,
    document_ids: list[uuid.UUID] | None = None,
    clause_types: list[str] | None = None,
    playbook_notes: str | None = None,
    playbook_id: uuid.UUID | None = None,
    provider: str | None = None,
    model: str | None = None,
    settings: Settings | None = None,
) -> ReviewTask:
    """Execute contract/clause review and persist verified ExtractedFinding rows."""
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
            limit=80,
        )
        if not chunks:
            raise ValueError(
                "No indexed document chunks found for contract review. "
                "Upload and index a contract first."
            )

        playbook = await resolve_playbook(session, playbook_id)
        rule_map = get_rule_map(playbook)
        combined_playbook_notes = ""
        if playbook is not None:
            combined_playbook_notes = format_playbook_notes(playbook)
        if playbook_notes and playbook_notes.strip():
            combined_playbook_notes = (
                f"{combined_playbook_notes}\n\nAdditional Notes:\n{playbook_notes.strip()}"
                if combined_playbook_notes
                else playbook_notes.strip()
            )

        targets = clause_types or STANDARD_CLAUSE_TYPES
        template_name, system_prompt, user_prompt = build_contract_review_prompt(
            chunks,
            clause_types=targets,
            playbook_notes=combined_playbook_notes or None,
        )
        llm_request = LLMRequest(
            task_kind="contract_review",
            prompt_template=template_name,
            prompt_version=PROMPT_VERSION,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            context_chunks=chunks,
            metadata={"clause_types": targets, "playbook_rules": rule_map},
        )
        llm_response = await router.generate_structured(
            llm_request,
            ContractReviewLLMOutput,
            provider=provider,
            model=model,
        )
        output = llm_response.data
        chunk_map = {c.chunk_id: c for c in chunks}

        created_finding_ids: list[str] = []
        grounding_scores: list[float] = []
        any_escalated = False

        for item in output.findings:
            verified = verify_citations(item.citations, chunk_map)
            is_missing = item.finding_type == "missing_protection"
            g_score = compute_grounding_score(verified, allow_empty=is_missing)
            unverified_cnt = sum(1 for v in verified if v.verification_status == "unverified")
            escalate, esc_reason = evaluate_escalation(
                confidence=item.confidence,
                grounding_score=g_score,
                severity=item.severity,
                unverified_count=unverified_cnt,
                settings=cfg,
                missing_protection=is_missing,
            )
            if escalate:
                any_escalated = True
            grounding_scores.append(g_score)

            primary_doc_id: uuid.UUID | None = None
            for cit in verified:
                if cit.document_id:
                    primary_doc_id = uuid.UUID(cit.document_id)
                    break
            if primary_doc_id is None and document_ids:
                primary_doc_id = document_ids[0]

            rule_entry = rule_map.get(item.category, {})
            proposed_redline = item.proposed_redline or (
                str(rule_entry.get("fallback_position"))
                if item.is_non_standard and rule_entry.get("fallback_position")
                else None
            )

            finding = ExtractedFinding(
                matter_id=matter_id,
                document_id=primary_doc_id,
                review_task_id=task.id,
                finding_type=item.finding_type,
                category=item.category,
                title=item.title[:255],
                summary=item.summary,
                severity=item.severity,
                confidence=item.confidence,
                grounding_score=g_score,
                requires_escalation=escalate,
                escalation_reason=esc_reason,
                recommended_action=item.recommended_action,
                citations_json=[v.model_dump() for v in verified],
                metadata_json={
                    "is_non_standard": item.is_non_standard,
                    "provider": llm_response.provider,
                    "model_name": llm_response.model_name,
                    "playbook_name": playbook.name if playbook else "Default Playbook",
                    "standard_position": rule_entry.get("standard_position"),
                    "proposed_redline": proposed_redline,
                },
                review_status="pending_review",
            )
            session.add(finding)
            await session.flush()
            created_finding_ids.append(str(finding.id))

        avg_grounding = (
            round(sum(grounding_scores) / len(grounding_scores), 3) if grounding_scores else 0.0
        )

        task.status = "completed"
        task.resolved_provider = llm_response.provider
        task.resolved_model = llm_response.model_name
        task.completed_at = datetime.now(UTC)
        task.result_json = {
            "executive_summary": output.executive_summary,
            "overall_risk_level": output.overall_risk_level,
            "finding_count": len(created_finding_ids),
            "finding_ids": created_finding_ids,
            "missing_protections": output.missing_protections,
            "uncertainties": output.uncertainties,
            "average_grounding_score": avg_grounding,
            "disclaimer": LEGAL_DISCLAIMER,
        }

        await record_audit_log(
            session,
            matter_id=matter_id,
            document_id=document_ids[0] if document_ids and len(document_ids) == 1 else None,
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
                "overall_risk_level": output.overall_risk_level,
                "finding_count": len(created_finding_ids),
                "fallback_used": llm_response.fallback_used,
                "fallback_errors": llm_response.fallback_errors,
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
        return task
    except Exception as exc:
        await session.rollback()
        failed_task = await session.get(ReviewTask, task_id)
        if failed_task is not None:
            failed_task.status = "failed"
            failed_task.error_message = str(exc)
            failed_task.completed_at = datetime.now(UTC)
            await session.commit()
        raise
