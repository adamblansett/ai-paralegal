import asyncio
import os
import uuid
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alembic import command
from app.core.config import Settings, clear_settings_cache
from app.core.rate_limit import SlidingWindowRateLimiter
from app.db.rls import load_rls_sql_statements, set_tenant_rls_context
from app.db.session import create_async_engine_from_url
from app.llm.embeddings import EmbeddingService
from app.models.matter import Matter
from app.models.organization import Organization
from app.services.events import MatterEventBus
from app.services.ingestion import process_document, upload_document
from app.services.retrieval import hybrid_search
from app.storage.local import LocalStorageBackend

POSTGRES_TEST_URL = os.getenv("POSTGRES_TEST_URL", "")
REDIS_TEST_URL = os.getenv("REDIS_URL", "")


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


@pytest.mark.integration
@pytest.mark.skipif(
    not POSTGRES_TEST_URL,
    reason="Set POSTGRES_TEST_URL to run live PostgreSQL RLS isolation tests.",
)
async def test_postgres_rls_enforces_tenant_isolation() -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    alembic_cfg = Config(str(backend_dir / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", POSTGRES_TEST_URL)
    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")

    engine = create_async_engine_from_url(POSTGRES_TEST_URL, use_null_pool=True)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with factory() as session:
            for stmt in load_rls_sql_statements():
                await session.execute(text(stmt))

            await session.execute(
                text(
                    "DO $$ BEGIN "
                    "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'paralegal_rls_tester') "
                    "THEN CREATE ROLE paralegal_rls_tester NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOBYPASSRLS NOLOGIN; "
                    "END IF; END $$;"
                )
            )
            await session.execute(
                text(
                    "GRANT SELECT, INSERT, UPDATE, DELETE "
                    "ON ALL TABLES IN SCHEMA public TO paralegal_rls_tester"
                )
            )

            org_a = Organization(
                slug=f"rls-org-a-{uuid.uuid4().hex[:6]}",
                name="RLS Alpha LLP",
            )
            org_b = Organization(
                slug=f"rls-org-b-{uuid.uuid4().hex[:6]}",
                name="RLS Beta LLP",
            )
            session.add_all([org_a, org_b])
            await session.flush()

            matter_a = Matter(
                organization_id=org_a.id,
                matter_number=f"RLSA-{uuid.uuid4().hex[:6].upper()}",
                title="Org A Confidential Matter",
                client_name="Client A",
            )
            matter_b = Matter(
                organization_id=org_b.id,
                matter_number=f"RLSB-{uuid.uuid4().hex[:6].upper()}",
                title="Org B Confidential Matter",
                client_name="Client B",
            )
            session.add_all([matter_a, matter_b])
            await session.commit()

            # Verify Tenant A RLS context hides Tenant B matter under NOSUPERUSER role
            await session.execute(text("SET LOCAL ROLE paralegal_rls_tester"))
            applied_a = await set_tenant_rls_context(session, org_a.id)
            assert applied_a is True
            visible_a = list(
                (
                    await session.execute(
                        select(Matter.id).where(Matter.id.in_([matter_a.id, matter_b.id]))
                    )
                )
                .scalars()
                .all()
            )
            assert visible_a == [matter_a.id]
            await session.commit()

            # After commit(), after_begin listener preserves org_a GUC automatically
            after_commit_guc = (
                await session.execute(
                    text("SELECT current_setting('app.current_organization_id', true)")
                )
            ).scalar_one()
            assert after_commit_guc == str(org_a.id)

            # Switch to Tenant B RLS context and verify isolation flips cleanly
            await session.execute(text("SET LOCAL ROLE paralegal_rls_tester"))
            applied_b = await set_tenant_rls_context(session, org_b.id)
            assert applied_b is True
            visible_b = list(
                (
                    await session.execute(
                        select(Matter.id).where(Matter.id.in_([matter_a.id, matter_b.id]))
                    )
                )
                .scalars()
                .all()
            )
            assert visible_b == [matter_b.id]
            await session.commit()
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.skipif(
    not REDIS_TEST_URL,
    reason="Set REDIS_URL to run live Redis ZSET rate limiter and Pub/Sub tests.",
)
async def test_live_redis_rate_limiter_and_pubsub_sse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limiter = SlidingWindowRateLimiter()
    bucket = f"live-redis-{uuid.uuid4().hex[:8]}"
    ok1, wait1 = await limiter.check_async(
        bucket, "client-1", limit=2, window_seconds=30, backend="redis", redis_url=REDIS_TEST_URL
    )
    ok2, wait2 = await limiter.check_async(
        bucket, "client-1", limit=2, window_seconds=30, backend="redis", redis_url=REDIS_TEST_URL
    )
    ok3, wait3 = await limiter.check_async(
        bucket, "client-1", limit=2, window_seconds=30, backend="redis", redis_url=REDIS_TEST_URL
    )
    assert (ok1, wait1) == (True, 0)
    assert (ok2, wait2) == (True, 0)
    assert ok3 is False
    assert wait3 >= 1

    monkeypatch.setenv("EVENT_BUS_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", REDIS_TEST_URL)
    clear_settings_cache()
    try:
        bus_sub = MatterEventBus()
        bus_pub = MatterEventBus()
        matter_id = uuid.uuid4()
        async with bus_sub.subscribe(matter_id) as queue:
            await asyncio.sleep(0.15)
            bus_pub.publish(matter_id, "document_indexed", {"document_id": "doc-live-1"})
            received = await asyncio.wait_for(queue.get(), timeout=3.0)
            assert received["event"] == "document_indexed"
            assert received["data"]["document_id"] == "doc-live-1"
    finally:
        clear_settings_cache()
