import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_app_settings,
    get_current_user,
    get_db,
    get_embeddings,
    get_router,
    require_matter_access,
)
from app.core.config import Settings
from app.core.rate_limit import rate_limit_llm
from app.llm.embeddings import EmbeddingService
from app.llm.router import ModelRouter
from app.models.review_task import ReviewTask
from app.models.user import User
from app.schemas.analysis import (
    BatchContractReviewRequest,
    ContractReviewRequest,
    DiscoverySynthesisRequest,
    DraftRequest,
    PortfolioRiskMatrixResponse,
    ResearchQueryRequest,
    ResearchQueryResponse,
    ReviewTaskRead,
    SearchHitRead,
    SearchRequest,
    SearchResponse,
)
from app.services.contract_review import build_portfolio_risk_matrix
from app.services.discovery import run_research_query, stream_research_query
from app.services.retrieval import hybrid_search
from app.workers.dispatch import (
    dispatch_batch_contract_review,
    dispatch_contract_review,
    dispatch_discovery_synthesis,
    dispatch_draft_generation,
)

router = APIRouter(tags=["analyses"])


@router.post(
    "/matters/{matter_id}/analyses/contract-review",
    response_model=ReviewTaskRead,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit_llm)],
)
async def trigger_contract_review(
    matter_id: uuid.UUID,
    payload: ContractReviewRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    model_router: ModelRouter = Depends(get_router),
) -> ReviewTaskRead:
    """Launch contract and clause risk analysis for a matter."""
    await require_matter_access(session, matter_id=matter_id, user=current_user, write=True)

    task = ReviewTask(
        matter_id=matter_id,
        document_id=(
            payload.document_ids[0]
            if payload.document_ids and len(payload.document_ids) == 1
            else None
        ),
        task_type="contract_review",
        status="queued",
        requested_provider=payload.provider,
        requested_model=payload.model,
        input_params_json=payload.model_dump(mode="json"),
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)

    await dispatch_contract_review(
        session=session,
        settings=settings,
        background_tasks=background_tasks,
        router=model_router,
        matter_id=matter_id,
        task=task,
        document_ids=payload.document_ids,
        clause_types=payload.clause_types,
        playbook_notes=payload.playbook_notes,
        playbook_id=payload.playbook_id,
        provider=payload.provider,
        model=payload.model,
    )
    await session.refresh(task)
    return ReviewTaskRead.model_validate(task)


@router.post(
    "/matters/{matter_id}/analyses/batch-contract-review",
    response_model=ReviewTaskRead,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit_llm)],
)
async def trigger_batch_contract_review(
    matter_id: uuid.UUID,
    payload: BatchContractReviewRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    model_router: ModelRouter = Depends(get_router),
) -> ReviewTaskRead:
    """Run contract review per document across a matter to build a Portfolio Risk Matrix."""
    await require_matter_access(session, matter_id=matter_id, user=current_user, write=True)

    task = ReviewTask(
        matter_id=matter_id,
        task_type="batch_contract_review",
        status="queued",
        requested_provider=payload.provider,
        requested_model=payload.model,
        input_params_json=payload.model_dump(mode="json"),
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)

    await dispatch_batch_contract_review(
        session=session,
        settings=settings,
        background_tasks=background_tasks,
        router=model_router,
        matter_id=matter_id,
        task=task,
        document_ids=payload.document_ids,
        clause_types=payload.clause_types,
        playbook_notes=payload.playbook_notes,
        playbook_id=payload.playbook_id,
        provider=payload.provider,
        model=payload.model,
    )
    await session.refresh(task)
    return ReviewTaskRead.model_validate(task)


@router.get("/matters/{matter_id}/analyses/risk-matrix", response_model=PortfolioRiskMatrixResponse)
async def get_portfolio_risk_matrix(
    matter_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> PortfolioRiskMatrixResponse:
    """Return Documents × Clause Categories risk matrix aggregated from contract findings."""
    await require_matter_access(session, matter_id=matter_id, user=current_user, write=False)
    try:
        return await build_portfolio_risk_matrix(session, matter_id=matter_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post(
    "/matters/{matter_id}/analyses/discovery-synthesis",
    response_model=ReviewTaskRead,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit_llm)],
)
async def trigger_discovery_synthesis(
    matter_id: uuid.UUID,
    payload: DiscoverySynthesisRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    model_router: ModelRouter = Depends(get_router),
) -> ReviewTaskRead:
    """Launch cross-document discovery synthesis across indexed matter records."""
    await require_matter_access(session, matter_id=matter_id, user=current_user, write=True)

    task = ReviewTask(
        matter_id=matter_id,
        task_type="discovery_synthesis",
        status="queued",
        requested_provider=payload.provider,
        requested_model=payload.model,
        input_params_json=payload.model_dump(mode="json"),
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)

    await dispatch_discovery_synthesis(
        session=session,
        settings=settings,
        background_tasks=background_tasks,
        router=model_router,
        matter_id=matter_id,
        task=task,
        document_ids=payload.document_ids,
        focus_topics=payload.focus_topics,
        provider=payload.provider,
        model=payload.model,
    )
    await session.refresh(task)
    return ReviewTaskRead.model_validate(task)


@router.post("/matters/{matter_id}/search", response_model=SearchResponse)
async def search_matter(
    matter_id: uuid.UUID,
    payload: SearchRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    embeddings: EmbeddingService = Depends(get_embeddings),
) -> SearchResponse:
    """Run hybrid lexical + vector search across matter chunks."""
    await require_matter_access(session, matter_id=matter_id, user=current_user, write=False)
    hits = await hybrid_search(
        session,
        embeddings,
        matter_id=matter_id,
        query=payload.query,
        document_ids=payload.document_ids,
        top_k=payload.top_k,
    )
    return SearchResponse(
        matter_id=matter_id,
        query=payload.query,
        hits=[
            SearchHitRead(
                chunk_id=h.chunk_id,
                document_id=h.document_id,
                document_filename=h.document_filename,
                document_type=h.document_type,
                chunk_index=h.chunk_index,
                content=h.content,
                page_number=h.page_number,
                paragraph_index=h.paragraph_index,
                section_heading=h.section_heading,
                char_start=h.char_start,
                char_end=h.char_end,
                lexical_score=h.lexical_score,
                semantic_score=h.semantic_score,
                rrf_score=h.rrf_score,
            )
            for h in hits
        ],
    )


@router.post(
    "/matters/{matter_id}/research/query",
    response_model=ResearchQueryResponse,
    dependencies=[Depends(rate_limit_llm)],
)
async def research_query(
    matter_id: uuid.UUID,
    payload: ResearchQueryRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    model_router: ModelRouter = Depends(get_router),
    embeddings: EmbeddingService = Depends(get_embeddings),
) -> ResearchQueryResponse:
    """Answer a legal research question strictly grounded in uploaded matter sources."""
    await require_matter_access(session, matter_id=matter_id, user=current_user, write=True)
    result = await run_research_query(
        session,
        model_router,
        embeddings,
        matter_id=matter_id,
        question=payload.question,
        document_ids=payload.document_ids,
        top_k=payload.top_k,
        provider=payload.provider,
        model=payload.model,
        save_to_queue=payload.save_to_queue,
        settings=settings,
    )
    return ResearchQueryResponse(**result)


@router.post(
    "/matters/{matter_id}/research/query/stream",
    dependencies=[Depends(rate_limit_llm)],
)
async def research_query_stream(
    matter_id: uuid.UUID,
    payload: ResearchQueryRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    model_router: ModelRouter = Depends(get_router),
    embeddings: EmbeddingService = Depends(get_embeddings),
) -> StreamingResponse:
    """Stream SSE research tokens live, followed by verified citations and audit log commit."""
    await require_matter_access(session, matter_id=matter_id, user=current_user, write=True)

    async def _sse_events() -> AsyncIterator[str]:
        async for event_name, event_data in stream_research_query(
            session,
            model_router,
            embeddings,
            matter_id=matter_id,
            question=payload.question,
            document_ids=payload.document_ids,
            top_k=payload.top_k,
            provider=payload.provider,
            model=payload.model,
            save_to_queue=payload.save_to_queue,
            settings=settings,
        ):
            yield f"event: {event_name}\ndata: {json.dumps(event_data)}\n\n"

    return StreamingResponse(
        _sse_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/matters/{matter_id}/drafts",
    response_model=ReviewTaskRead,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit_llm)],
)
async def generate_draft(
    matter_id: uuid.UUID,
    payload: DraftRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    model_router: ModelRouter = Depends(get_router),
    embeddings: EmbeddingService = Depends(get_embeddings),
) -> ReviewTaskRead:
    """Generate a citation-grounded legal draft requiring attorney review."""
    await require_matter_access(session, matter_id=matter_id, user=current_user, write=True)

    task = ReviewTask(
        matter_id=matter_id,
        task_type="draft_generation",
        status="queued",
        requested_provider=payload.provider,
        requested_model=payload.model,
        input_params_json=payload.model_dump(mode="json"),
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)

    await dispatch_draft_generation(
        session=session,
        settings=settings,
        background_tasks=background_tasks,
        router=model_router,
        embeddings=embeddings,
        matter_id=matter_id,
        task=task,
        draft_type=payload.draft_type,
        instructions=payload.instructions,
        document_ids=payload.document_ids,
        provider=payload.provider,
        model=payload.model,
    )
    await session.refresh(task)
    return ReviewTaskRead.model_validate(task)


@router.get("/matters/{matter_id}/tasks", response_model=list[ReviewTaskRead])
async def list_matter_tasks(
    matter_id: uuid.UUID,
    task_type: str | None = None,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> list[ReviewTaskRead]:
    """List analysis and ingestion tasks for a matter."""
    await require_matter_access(session, matter_id=matter_id, user=current_user, write=False)
    stmt = (
        select(ReviewTask)
        .where(ReviewTask.matter_id == matter_id)
        .order_by(ReviewTask.created_at.desc())
    )
    if task_type:
        stmt = stmt.where(ReviewTask.task_type == task_type)
    tasks = (await session.execute(stmt)).scalars().all()
    return [ReviewTaskRead.model_validate(t) for t in tasks]


@router.get("/tasks/{task_id}", response_model=ReviewTaskRead)
async def get_task(
    task_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> ReviewTaskRead:
    """Fetch status and structured result for a background review or ingestion task."""
    task = await session.get(ReviewTask, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found.")
    await require_matter_access(
        session, matter_id=task.matter_id, user=current_user, write=False
    )
    return ReviewTaskRead.model_validate(task)
