"""Add users, matter_memberships, and playbooks tables for RBAC and contract redlines.

Revision ID: 0002_auth_playbooks_rbac
Revises: 0001_initial_schema
Create Date: 2026-11-22 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_auth_playbooks_rbac"
down_revision: str | Sequence[str] | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB_COMPAT = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
    op.create_index(op.f("ix_users_role"), "users", ["role"], unique=False)

    op.create_table(
        "matter_memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("matter_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("access_level", sa.String(length=32), nullable=False),
        sa.Column("added_by_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["added_by_id"],
            ["users.id"],
            name=op.f("fk_matter_memberships_added_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["matter_id"],
            ["matters.id"],
            name=op.f("fk_matter_memberships_matter_id_matters"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_matter_memberships_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_matter_memberships")),
        sa.UniqueConstraint("matter_id", "user_id", name="uq_matter_memberships_matter_user"),
    )
    op.create_index(
        op.f("ix_matter_memberships_matter_id"),
        "matter_memberships",
        ["matter_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_matter_memberships_user_id"),
        "matter_memberships",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "playbooks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("practice_area", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("rules_json", JSONB_COMPAT, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_playbooks_created_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_playbooks")),
    )
    op.create_index(op.f("ix_playbooks_name"), "playbooks", ["name"], unique=True)
    op.create_index(op.f("ix_playbooks_is_default"), "playbooks", ["is_default"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_playbooks_is_default"), table_name="playbooks")
    op.drop_index(op.f("ix_playbooks_name"), table_name="playbooks")
    op.drop_table("playbooks")

    op.drop_index(op.f("ix_matter_memberships_user_id"), table_name="matter_memberships")
    op.drop_index(op.f("ix_matter_memberships_matter_id"), table_name="matter_memberships")
    op.drop_table("matter_memberships")

    op.drop_index(op.f("ix_users_role"), table_name="users")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
