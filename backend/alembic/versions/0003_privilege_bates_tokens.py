"""Add Bates numbering, privilege/PII metadata on documents, and token revocation tables.

Revision ID: 0003_privilege_bates_tokens
Revises: 0002_auth_playbooks_rbac
Create Date: 2026-11-22 12:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_privilege_bates_tokens"
down_revision: str | Sequence[str] | None = "0002_auth_playbooks_rbac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB_COMPAT = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.add_column("documents", sa.Column("bates_start", sa.String(length=64), nullable=True))
    op.add_column("documents", sa.Column("bates_end", sa.String(length=64), nullable=True))
    op.add_column(
        "documents",
        sa.Column(
            "privilege_status",
            sa.String(length=32),
            nullable=False,
            server_default="none",
        ),
    )
    op.add_column("documents", sa.Column("privilege_reason", sa.Text(), nullable=True))
    op.add_column(
        "documents",
        sa.Column(
            "pii_detected_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "documents",
        sa.Column(
            "pii_summary_json",
            JSONB_COMPAT,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.create_index(op.f("ix_documents_bates_start"), "documents", ["bates_start"], unique=False)
    op.create_index(
        op.f("ix_documents_privilege_status"),
        "documents",
        ["privilege_status"],
        unique=False,
    )

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_refresh_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
    )
    op.create_index(
        op.f("ix_refresh_tokens_token_hash"), "refresh_tokens", ["token_hash"], unique=True
    )
    op.create_index(
        op.f("ix_refresh_tokens_user_id"), "refresh_tokens", ["user_id"], unique=False
    )

    op.create_table(
        "revoked_tokens",
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_revoked_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("jti", name=op.f("pk_revoked_tokens")),
    )
    op.create_index(
        op.f("ix_revoked_tokens_user_id"), "revoked_tokens", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_revoked_tokens_user_id"), table_name="revoked_tokens")
    op.drop_table("revoked_tokens")

    op.drop_index(op.f("ix_refresh_tokens_user_id"), table_name="refresh_tokens")
    op.drop_index(op.f("ix_refresh_tokens_token_hash"), table_name="refresh_tokens")
    op.drop_table("refresh_tokens")

    op.drop_index(op.f("ix_documents_privilege_status"), table_name="documents")
    op.drop_index(op.f("ix_documents_bates_start"), table_name="documents")
    op.drop_column("documents", "pii_summary_json")
    op.drop_column("documents", "pii_detected_count")
    op.drop_column("documents", "privilege_reason")
    op.drop_column("documents", "privilege_status")
    op.drop_column("documents", "bates_end")
    op.drop_column("documents", "bates_start")
