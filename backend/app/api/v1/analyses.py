import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_app_settings, get_db, get_embeddings, get_router
from app.core.config import Settings
from app.llm.embeddings import EmbeddingService
from app.llm.router import ModelRouter
from app.models.matter import Matter
from app.models.review_task import ReviewTask
from app.schemas.analysis import (
    ContractReviewRequest,
    DiscoverySynthesisRequest,
    DraftRequest,
    ResearchQueryRequest,
    ResearchQueryResponse,
    ReviewTaskRead,
    SearchHitRead,
    SearchRequest,
    SearchResponse,
)
from app.services.discovery import run_research_query
from app.services.retrieval import hybrid_search
from app.workers.dispatch import (
    dispatch_contract_review,
    dispatch_discovery_synthesis,
    dispatch_draft_generation,
)

router = APIRouter(tags=["analyses"])


async def _ensure_matter(session: AsyncSession, matter_id: uuid.UUID) -> Matter:
    matter = await session.get(Matter, matter_id)
    if matter is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Matter not found.")
    return matter


@router.post(
    "/matters/{matter_id}/analyses/contract-review",
    response_model=ReviewTaskRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_contract_review(
    matter_id: uuid.UUID,
    payload: ContractReviewRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    model_router: ModelRouter = Depends(get_router),
) -> ReviewTaskRead:
    """Launch contract and clause risk analysis for a matter."""
    await _ensure_matter(session, matter_id)

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
        provider=payload.provider,
        model=payload.model,
    )
    await session.refresh(task)
    return ReviewTaskRead.model_validate(task)


@router.post(
    "/matters/{matter_id}/analyses/discovery-synthesis",
    response_model=ReviewTaskRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_discovery_synthesis(
    matter_id: uuid.UUID,
    payload: DiscoverySynthesisRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    model_router: ModelRouter = Depends(get_router),
) -> ReviewTaskRead:
    """Launch cross-document discovery synthesis across indexed matter records."""
    await _ensure_matter(session, matter_id)

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
    session: AsyncSession = Depends(get_db),
    embeddings: EmbeddingService = Depends(get_embeddings),
) -> SearchResponse:
    """Run hybrid lexical + vector search across matter chunks."""
    await _ensure_matter(session, matter_id)
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


@router.post("/matters/{matter_id}/research/query", response_model=ResearchQueryResponse)
async def research_query(
    matter_id: uuid.UUID,
    payload: ResearchQueryRequest,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    model_router: ModelRouter = Depends(get_router),
    embeddings: EmbeddingService = Depends(get_embeddings),
) -> ResearchQueryResponse:
    """Answer a legal research question strictly grounded in uploaded matter sources."""
    await _ensure_matter(session, matter_id)
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
    "/matters/{matter_id}/drafts",
    response_model=ReviewTaskRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_draft(
    matter_id: uuid.UUID,
    payload: DraftRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    model_router: ModelRouter = Depends(get_router),
    embeddings: EmbeddingService = Depends(get_embeddings),
) -> ReviewTaskRead:
    """Generate a citation-grounded legal draft requiring attorney review."""
    await _ensure_matter(session, matter_id)

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
    session: AsyncSession = Depends(get_db),
) -> list[ReviewTaskRead]:
    """List analysis and ingestion tasks for a matter."""
    await _ensure_matter(session, matter_id)
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
    session: AsyncSession = Depends(get_db),
) -> ReviewTaskRead:
    """Fetch status and structured result for a background review or ingestion task."""
    task = await session.get(ReviewTask, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found.")
    return ReviewTaskRead.model_validate(task)
