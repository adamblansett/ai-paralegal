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
        tables = set(inspect(sync_engine).get_table_names())
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
    }
    assert expected.issubset(tables)
