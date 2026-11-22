from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command


def test_alembic_upgrade_head_creates_all_core_tables(tmp_path: Path) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "alembic_verify.db"

    alembic_cfg = Config(str(backend_dir / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path.as_posix()}")

    command.upgrade(alembic_cfg, "head")

    sync_engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        inspector = inspect(sync_engine)
        tables = set(inspector.get_table_names())
        doc_columns = {col["name"] for col in inspector.get_columns("documents")}
    finally:
        sync_engine.dispose()

    expected = {
        "matters",
        "documents",
        "document_chunks",
        "review_tasks",
        "extracted_findings",
        "audit_logs",
        "users",
        "matter_memberships",
        "playbooks",
        "refresh_tokens",
        "revoked_tokens",
    }
    assert expected.issubset(tables)
    assert {
        "bates_start",
        "bates_end",
        "privilege_status",
        "privilege_reason",
        "pii_detected_count",
        "pii_summary_json",
    }.issubset(doc_columns)
