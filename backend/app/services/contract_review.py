import uuid
from datetime import UTC, datetime

from sqlalchemy import select
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
from app.models.document import Document
from app.models.finding import ExtractedFinding
from app.models.matter import Matter
from app.models.review_task import ReviewTask
from app.schemas.analysis import (
    MatrixCellRead,
    MatrixDocumentRowRead,
    PortfolioRiskMatrixResponse,
)
from app.services.audit import record_audit_log
from app.services.events import event_bus
from app.services.playbooks import format_playbook_notes, get_rule_map, resolve_playbook
from app.services.retrieval import load_matter_chunks

_SEVERITY_RANK: dict[str, int] = {
    "critical": 5,
    "high": 4,
    "medium": 3,
    "low": 2,
    "info": 1,
    "none": 0,
}

_STATUS_PRIORITY: dict[str, int] = {
    "flagged": 3,
    "pending_review": 2,
    "approved": 1,
}


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
    event_bus.publish(
        matter_id,
        "task_progress",
        {
            "task_id": str(task.id),
            "task_type": task.task_type,
            "status": "running",
            "message": "Contract review analysis started",
        },
    )

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
        event_bus.publish(
            matter_id,
            "task_completed",
            {
                "task_id": str(task.id),
                "task_type": task.task_type,
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
                "task_type": "contract_review",
                "status": "failed",
                "error_message": str(exc),
            },
        )
        raise


async def run_batch_contract_review(
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
    """Analyze each indexed document in a matter sequentially to build a Risk Matrix."""
    cfg = settings or get_settings()
    parent_task = await session.get(ReviewTask, task_id)
    if parent_task is None:
        raise ValueError(f"ReviewTask {task_id} not found.")

    parent_task.status = "running"
    parent_task.started_at = datetime.now(UTC)
    parent_task.error_message = None
    await session.commit()

    try:
        stmt = (
            select(Document)
            .where(Document.matter_id == matter_id, Document.ingestion_status == "indexed")
            .order_by(Document.created_at.asc(), Document.filename.asc())
        )
        if document_ids:
            stmt = stmt.where(Document.id.in_(document_ids))
        target_docs = list((await session.execute(stmt)).scalars().all())
        if not target_docs:
            raise ValueError("No indexed documents available for batch contract review.")

        child_task_ids: list[str] = []
        total_findings = 0
        resolved_provider: str | None = None
        resolved_model: str | None = None

        total_docs = len(target_docs)
        for idx, doc in enumerate(target_docs, start=1):
            event_bus.publish(
                matter_id,
                "batch_matrix_progress",
                {
                    "task_id": str(parent_task.id),
                    "completed_docs": idx - 1,
                    "total_docs": total_docs,
                    "current_document": doc.filename,
                },
            )
            child = ReviewTask(
                matter_id=matter_id,
                document_id=doc.id,
                task_type="contract_review",
                status="queued",
                requested_provider=provider,
                requested_model=model,
                input_params_json={
                    "parent_batch_task_id": str(parent_task.id),
                    "document_ids": [str(doc.id)],
                    "clause_types": clause_types,
                    "playbook_id": str(playbook_id) if playbook_id else None,
                },
            )
            session.add(child)
            await session.commit()
            await session.refresh(child)

            completed_child = await run_contract_review(
                session,
                router,
                matter_id=matter_id,
                task_id=child.id,
                document_ids=[doc.id],
                clause_types=clause_types,
                playbook_notes=playbook_notes,
                playbook_id=playbook_id,
                provider=provider,
                model=model,
                settings=cfg,
            )
            child_task_ids.append(str(completed_child.id))
            total_findings += int((completed_child.result_json or {}).get("finding_count", 0))
            resolved_provider = completed_child.resolved_provider or resolved_provider
            resolved_model = completed_child.resolved_model or resolved_model

            event_bus.publish(
                matter_id,
                "batch_matrix_progress",
                {
                    "task_id": str(parent_task.id),
                    "completed_docs": idx,
                    "total_docs": total_docs,
                    "current_document": doc.filename,
                },
            )

        parent_refreshed = await session.get(ReviewTask, task_id)
        if parent_refreshed is None:
            raise ValueError(f"ReviewTask {task_id} disappeared during execution.")

        parent_refreshed.status = "completed"
        parent_refreshed.resolved_provider = resolved_provider
        parent_refreshed.resolved_model = resolved_model
        parent_refreshed.completed_at = datetime.now(UTC)
        parent_refreshed.result_json = {
            "processed_documents": total_docs,
            "child_task_ids": child_task_ids,
            "total_findings": total_findings,
            "disclaimer": LEGAL_DISCLAIMER,
        }
        await session.commit()
        await session.refresh(parent_refreshed)
        event_bus.publish(
            matter_id,
            "task_completed",
            {
                "task_id": str(parent_refreshed.id),
                "task_type": "batch_contract_review",
                "status": "completed",
                "processed_documents": total_docs,
                "finding_count": total_findings,
            },
        )
        return parent_refreshed
    except Exception as exc:
        await session.rollback()
        failed_parent = await session.get(ReviewTask, task_id)
        if failed_parent is not None:
            failed_parent.status = "failed"
            failed_parent.error_message = str(exc)
            failed_parent.completed_at = datetime.now(UTC)
            await session.commit()
        event_bus.publish(
            matter_id,
            "task_failed",
            {
                "task_id": str(task_id),
                "task_type": "batch_contract_review",
                "status": "failed",
                "error_message": str(exc),
            },
        )
        raise


async def build_portfolio_risk_matrix(
    session: AsyncSession,
    *,
    matter_id: uuid.UUID,
) -> PortfolioRiskMatrixResponse:
    """Aggregate contract findings into a Documents × Clause Categories Risk Matrix."""
    matter = await session.get(Matter, matter_id)
    if matter is None:
        raise ValueError(f"Matter {matter_id} not found.")

    doc_stmt = (
        select(Document)
        .where(Document.matter_id == matter_id)
        .order_by(Document.created_at.asc(), Document.filename.asc())
    )
    docs = list((await session.execute(doc_stmt)).scalars().all())

    finding_stmt = (
        select(ExtractedFinding)
        .where(
            ExtractedFinding.matter_id == matter_id,
            ExtractedFinding.finding_type.in_(["clause", "risk_flag", "missing_protection"]),
        )
        .order_by(ExtractedFinding.created_at.asc())
    )
    findings = list((await session.execute(finding_stmt)).scalars().all())

    category_order: list[str] = list(STANDARD_CLAUSE_TYPES)
    for f in findings:
        if f.category not in category_order:
            category_order.append(f.category)

    by_doc: dict[uuid.UUID, list[ExtractedFinding]] = {doc.id: [] for doc in docs}
    for f in findings:
        target_doc_id = f.document_id
        if target_doc_id is None and f.citations_json:
            raw_did = f.citations_json[0].get("document_id")
            if raw_did:
                try:
                    target_doc_id = uuid.UUID(str(raw_did))
                except ValueError:
                    target_doc_id = None
        if target_doc_id is not None and target_doc_id in by_doc:
            by_doc[target_doc_id].append(f)

    rows: list[MatrixDocumentRowRead] = []
    high_risk_docs = 0

    for doc in docs:
        doc_findings = by_doc.get(doc.id, [])
        grouped: dict[str, list[ExtractedFinding]] = {}
        for f in doc_findings:
            grouped.setdefault(f.category, []).append(f)

        cells: dict[str, MatrixCellRead] = {}
        doc_max_severity = "none" if not doc_findings else "info"
        for cat in category_order:
            cat_findings = grouped.get(cat, [])
            if not cat_findings:
                continue
            top_f = max(cat_findings, key=lambda x: _SEVERITY_RANK.get(x.severity, 0))
            worst_sev = top_f.severity
            if _SEVERITY_RANK.get(worst_sev, 0) > _SEVERITY_RANK.get(doc_max_severity, 0):
                doc_max_severity = worst_sev
            review_state = max(
                (cf.review_status for cf in cat_findings),
                key=lambda s: _STATUS_PRIORITY.get(s, 0),
            )
            cells[cat] = MatrixCellRead(
                severity=worst_sev,
                finding_count=len(cat_findings),
                review_status=review_state,
                escalated=any(cf.requires_escalation for cf in cat_findings),
                finding_ids=[cf.id for cf in cat_findings],
                top_title=top_f.title,
            )

        if doc_max_severity in {"critical", "high"}:
            high_risk_docs += 1

        bates_range = (
            f"{doc.bates_start}–{doc.bates_end}"
            if doc.bates_start and doc.bates_end
            else doc.bates_start
        )
        rows.append(
            MatrixDocumentRowRead(
                document_id=doc.id,
                filename=doc.filename,
                bates_range=bates_range,
                privilege_status=doc.privilege_status,
                overall_risk=doc_max_severity,
                finding_count=len(doc_findings),
                escalated_count=sum(1 for df in doc_findings if df.requires_escalation),
                cells=cells,
            )
        )

    return PortfolioRiskMatrixResponse(
        matter_id=matter_id,
        categories=category_order,
        rows=rows,
        total_documents=len(docs),
        total_findings=len(findings),
        high_risk_documents=high_risk_docs,
    )
