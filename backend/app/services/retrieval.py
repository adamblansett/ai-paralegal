import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.embeddings import EmbeddingService, cosine_similarity
from app.llm.types import SourceChunkContext
from app.models.document import Document, DocumentChunk

_QUERY_TOKEN_RE = re.compile(r"[a-z0-9]{2,}")


@dataclass(slots=True)
class RetrievedChunk:
    """A ranked document chunk returned by hybrid retrieval."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_filename: str
    document_type: str
    chunk_index: int
    content: str
    page_number: int | None
    paragraph_index: int | None
    section_heading: str | None
    char_start: int | None
    char_end: int | None
    lexical_score: float
    semantic_score: float
    rrf_score: float


def chunks_to_source_contexts(items: list[RetrievedChunk]) -> list[SourceChunkContext]:
    """Convert retrieved chunks to numbered SourceChunkContext objects for prompts."""
    return [
        SourceChunkContext(
            chunk_ref=f"C{idx}",
            chunk_id=str(item.chunk_id),
            document_id=str(item.document_id),
            document_filename=item.document_filename,
            document_type=item.document_type,
            chunk_index=item.chunk_index,
            page_number=item.page_number,
            paragraph_index=item.paragraph_index,
            section_heading=item.section_heading,
            char_start=item.char_start,
            char_end=item.char_end,
            content=item.content,
        )
        for idx, item in enumerate(items, start=1)
    ]


async def load_matter_chunks(
    session: AsyncSession,
    *,
    matter_id: uuid.UUID,
    document_ids: list[uuid.UUID] | None = None,
    limit: int = 80,
) -> list[SourceChunkContext]:
    """Load ordered indexed document chunks for a matter (optionally filtered by documents)."""
    stmt = (
        select(DocumentChunk, Document.filename, Document.document_type)
        .join(Document, DocumentChunk.document_id == Document.id)
        .where(
            DocumentChunk.matter_id == matter_id,
            Document.ingestion_status == "indexed",
        )
        .order_by(Document.created_at.asc(), DocumentChunk.chunk_index.asc())
        .limit(limit)
    )
    if document_ids:
        stmt = stmt.where(DocumentChunk.document_id.in_(document_ids))

    rows = (await session.execute(stmt)).all()
    contexts: list[SourceChunkContext] = []
    for idx, (chunk, filename, doc_type) in enumerate(rows, start=1):
        contexts.append(
            SourceChunkContext(
                chunk_ref=f"C{idx}",
                chunk_id=str(chunk.id),
                document_id=str(chunk.document_id),
                document_filename=filename,
                document_type=doc_type,
                chunk_index=chunk.chunk_index,
                page_number=chunk.page_number,
                paragraph_index=chunk.paragraph_index,
                section_heading=chunk.section_heading,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                content=chunk.content,
            )
        )
    return contexts


def _lexical_score(query: str, content: str, heading: str | None) -> float:
    tokens = _QUERY_TOKEN_RE.findall(query.lower())
    if not tokens:
        return 0.0
    haystack = f"{heading or ''} {content}".lower()
    unique_hits = sum(1 for tok in set(tokens) if tok in haystack)
    term_freq = sum(haystack.count(tok) for tok in tokens)
    phrase_bonus = 2.0 if query.strip().lower() in haystack else 0.0
    return (unique_hits / len(set(tokens))) * 2.0 + min(term_freq, 12) * 0.15 + phrase_bonus


async def _postgres_hybrid_search(
    session: AsyncSession,
    *,
    matter_id: uuid.UUID,
    query: str,
    query_vec: list[float],
    document_ids: list[uuid.UUID] | None,
    top_k: int,
) -> list[RetrievedChunk]:
    vec_literal = "[" + ",".join(f"{v:.6f}" for v in query_vec) + "]"
    doc_filter = "AND dc.document_id = ANY(:doc_ids)" if document_ids else ""

    fts_sql = text(
        f"""
        SELECT dc.id,
               ts_rank_cd(to_tsvector('english', dc.content),
                          plainto_tsquery('english', :q)) AS lex_score
        FROM document_chunks dc
        JOIN documents d ON d.id = dc.document_id
        WHERE dc.matter_id = :matter_id
          AND d.ingestion_status = 'indexed'
          {doc_filter}
        ORDER BY lex_score DESC, dc.chunk_index ASC
        LIMIT :candidate_limit
        """
    )
    vec_sql = text(
        f"""
        SELECT dc.id, (1.0 - (dc.embedding <=> CAST(:qvec AS vector))) AS sem_score
        FROM document_chunks dc
        JOIN documents d ON d.id = dc.document_id
        WHERE dc.matter_id = :matter_id
          AND d.ingestion_status = 'indexed'
          AND dc.embedding IS NOT NULL
          {doc_filter}
        ORDER BY dc.embedding <=> CAST(:qvec AS vector) ASC
        LIMIT :candidate_limit
        """
    )
    params: dict[str, object] = {
        "matter_id": str(matter_id),
        "q": query,
        "qvec": vec_literal,
        "candidate_limit": max(top_k * 3, 24),
    }
    if document_ids:
        params["doc_ids"] = [str(d) for d in document_ids]

    lex_rows = (await session.execute(fts_sql, params)).all()
    vec_rows = (await session.execute(vec_sql, params)).all()

    rrf_k = 60.0
    scores: dict[uuid.UUID, dict[str, float]] = {}

    for rank, (cid, score) in enumerate(lex_rows, start=1):
        uid = cid if isinstance(cid, uuid.UUID) else uuid.UUID(str(cid))
        entry = scores.setdefault(uid, {"lex": 0.0, "sem": 0.0, "rrf": 0.0})
        entry["lex"] = float(score or 0.0)
        entry["rrf"] += 1.0 / (rrf_k + rank)

    for rank, (cid, score) in enumerate(vec_rows, start=1):
        uid = cid if isinstance(cid, uuid.UUID) else uuid.UUID(str(cid))
        entry = scores.setdefault(uid, {"lex": 0.0, "sem": 0.0, "rrf": 0.0})
        entry["sem"] = float(score or 0.0)
        entry["rrf"] += 1.0 / (rrf_k + rank)

    if not scores:
        return []

    top_ids = sorted(scores.keys(), key=lambda uid: scores[uid]["rrf"], reverse=True)[:top_k]
    stmt = (
        select(DocumentChunk, Document.filename, Document.document_type)
        .join(Document, DocumentChunk.document_id == Document.id)
        .where(DocumentChunk.id.in_(top_ids))
    )
    rows = (await session.execute(stmt)).all()
    by_id = {chunk.id: (chunk, filename, doc_type) for chunk, filename, doc_type in rows}

    results: list[RetrievedChunk] = []
    for uid in top_ids:
        if uid not in by_id:
            continue
        chunk, filename, doc_type = by_id[uid]
        s = scores[uid]
        results.append(
            RetrievedChunk(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                document_filename=filename,
                document_type=doc_type,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                page_number=chunk.page_number,
                paragraph_index=chunk.paragraph_index,
                section_heading=chunk.section_heading,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                lexical_score=round(s["lex"], 4),
                semantic_score=round(s["sem"], 4),
                rrf_score=round(s["rrf"], 6),
            )
        )
    return results


async def hybrid_search(
    session: AsyncSession,
    embedding_service: EmbeddingService,
    *,
    matter_id: uuid.UUID,
    query: str,
    document_ids: list[uuid.UUID] | None = None,
    top_k: int = 8,
) -> list[RetrievedChunk]:
    """Run hybrid lexical + semantic search fused via Reciprocal Rank Fusion (RRF)."""
    query_vec = await embedding_service.embed_text(query)

    bind = session.get_bind()
    dialect_name = bind.dialect.name if bind is not None else "sqlite"
    if dialect_name == "postgresql":
        try:
            pg_results = await _postgres_hybrid_search(
                session,
                matter_id=matter_id,
                query=query,
                query_vec=query_vec,
                document_ids=document_ids,
                top_k=top_k,
            )
            if pg_results:
                return pg_results
        except Exception:
            # Fall back to in-process scoring if pgvector extension/indexes are unavailable
            pass

    stmt = (
        select(DocumentChunk, Document.filename, Document.document_type)
        .join(Document, DocumentChunk.document_id == Document.id)
        .where(
            DocumentChunk.matter_id == matter_id,
            Document.ingestion_status == "indexed",
        )
    )
    if document_ids:
        stmt = stmt.where(DocumentChunk.document_id.in_(document_ids))

    rows = (await session.execute(stmt)).all()
    if not rows:
        return []

    scored_items: list[tuple[DocumentChunk, str, str, float, float]] = []
    for chunk, filename, doc_type in rows:
        lex = _lexical_score(query, chunk.content, chunk.section_heading)
        sem = cosine_similarity(query_vec, chunk.embedding)
        scored_items.append((chunk, filename, doc_type, lex, sem))

    lex_ranked = sorted(scored_items, key=lambda x: x[3], reverse=True)
    sem_ranked = sorted(scored_items, key=lambda x: x[4], reverse=True)

    rrf_k = 60.0
    rrf_map: dict[uuid.UUID, float] = {}
    for rank, item in enumerate(lex_ranked, start=1):
        rrf_map[item[0].id] = rrf_map.get(item[0].id, 0.0) + (1.0 / (rrf_k + rank))
    for rank, item in enumerate(sem_ranked, start=1):
        rrf_map[item[0].id] = rrf_map.get(item[0].id, 0.0) + (1.0 / (rrf_k + rank))

    ranked = sorted(scored_items, key=lambda x: rrf_map[x[0].id], reverse=True)[:top_k]
    return [
        RetrievedChunk(
            chunk_id=chunk.id,
            document_id=chunk.document_id,
            document_filename=filename,
            document_type=doc_type,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            page_number=chunk.page_number,
            paragraph_index=chunk.paragraph_index,
            section_heading=chunk.section_heading,
            char_start=chunk.char_start,
            char_end=chunk.char_end,
            lexical_score=round(lex, 4),
            semantic_score=round(sem, 4),
            rrf_score=round(rrf_map[chunk.id], 6),
        )
        for chunk, filename, doc_type, lex, sem in ranked
    ]
