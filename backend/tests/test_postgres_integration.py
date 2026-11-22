import asyncio
import os
import uuid
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alembic import command
from app.core.config import Settings
from app.db.session import create_async_engine_from_url
from app.llm.embeddings import EmbeddingService
from app.models.matter import Matter
from app.services.ingestion import process_document, upload_document
from app.services.retrieval import hybrid_search
from app.storage.local import LocalStorageBackend

POSTGRES_TEST_URL = os.getenv("POSTGRES_TEST_URL", "")


@pytest.mark.integration
@pytest.mark.skipif(
    not POSTGRES_TEST_URL,
    reason="Set POSTGRES_TEST_URL to run live PostgreSQL pgvector + Alembic integration tests.",
)
async def test_postgres_alembic_pgvector_hybrid_search(tmp_path: Path) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    alembic_cfg = Config(str(backend_dir / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", POSTGRES_TEST_URL)
    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")

    engine = create_async_engine_from_url(POSTGRES_TEST_URL, use_null_pool=True)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    storage = LocalStorageBackend(tmp_path / "pg_storage")
    cfg = Settings(
        database_url=POSTGRES_TEST_URL,
        embedding_provider="deterministic",
        embedding_dimensions=384,
    )
    embeddings = EmbeddingService(cfg)

    try:
        async with factory() as session:
            ext_row = (
                await session.execute(
                    text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
                )
            ).first()
            assert ext_row is not None

            matter = Matter(
                matter_number=f"PG-{uuid.uuid4().hex[:6].upper()}",
                title="PostgreSQL Integration Matter",
                client_name="Integration Client",
                practice_area="Commercial Contracts",
            )
            session.add(matter)
            await session.commit()
            await session.refresh(matter)

            doc, task = await upload_document(
                session,
                storage,
                matter_id=matter.id,
                filename="msa_pg.txt",
                content_type="text/plain",
                data=(
                    b"SECTION 7. LIMITATION OF LIABILITY\n"
                    b"Aggregate liability shall not exceed $20,000 or one month of fees."
                ),
                document_type="contract",
            )
            await process_document(
                session,
                storage,
                embeddings,
                document_id=doc.id,
                review_task_id=task.id,
            )

            hits = await hybrid_search(
                session,
                embeddings,
                matter_id=matter.id,
                query="aggregate liability $20,000",
                top_k=3,
            )
            assert len(hits) >= 1
            assert hits[0].rrf_score > 0
    finally:
        await engine.dispose()
