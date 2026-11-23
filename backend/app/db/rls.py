import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession


def _attach_rls_listener(session: AsyncSession) -> None:
    """Ensure every subsequent SQLAlchemy transaction on this session inherits the tenant GUC."""
    if session.info.get("_rls_listener_attached"):
        return

    sync_session = session.sync_session

    @event.listens_for(sync_session, "after_begin")
    def _set_org_on_begin(_sess: Any, _transaction: Any, connection: Any) -> None:
        org_id = session.info.get("tenant_organization_id")
        if org_id is not None and connection.dialect.name == "postgresql":
            connection.execute(
                text("SELECT set_config('app.current_organization_id', :org_id, true)"),
                {"org_id": str(org_id)},
            )

    session.info["_rls_listener_attached"] = True


async def set_tenant_rls_context(
    session: AsyncSession,
    organization_id: uuid.UUID | None,
) -> bool:
    """Bind `app.current_organization_id` for the active PostgreSQL transaction.

    Returns ``True`` when applied to a PostgreSQL connection, or ``False`` on SQLite/no-op
    engines where Row-Level Security GUCs do not apply.
    """
    org_value = str(organization_id) if organization_id else ""
    session.info["tenant_organization_id"] = org_value

    try:
        bind = session.get_bind()
    except Exception:
        return False

    dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
    if dialect_name != "postgresql":
        return False

    _attach_rls_listener(session)
    await session.execute(
        text("SELECT set_config('app.current_organization_id', :org_id, true)"),
        {"org_id": org_value},
    )
    return True


def load_rls_sql_statements() -> list[str]:
    """Load and split executable SQL statements from `deploy/sql/rls_policies.sql`."""
    sql_path = Path(__file__).resolve().parents[3] / "deploy" / "sql" / "rls_policies.sql"
    raw = sql_path.read_text(encoding="utf-8")
    cleaned_lines = [
        line for line in raw.splitlines() if not line.strip().startswith("--")
    ]
    cleaned_sql = "\n".join(cleaned_lines)
    return [stmt.strip() for stmt in cleaned_sql.split(";") if stmt.strip()]
