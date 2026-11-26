"""Add matter_obligations, ethical_walls, and User MFA / SCIM fields.

Revision ID: 0005_legal_ops_mfa_walls
Revises: 0004_enterprise_governance
Create Date: 2026-11-26 07:15:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_legal_ops_mfa_walls"
down_revision: str | Sequence[str] | None = "0004_enterprise_governance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(sa.Column("mfa_secret", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("scim_external_id", sa.String(length=128), nullable=True))
        batch_op.create_index(op.f("ix_users_scim_external_id"), ["scim_external_id"], unique=False)

    op.create_table(
        "matter_obligations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("matter_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False, server_default="compliance"),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notice_window_days", sa.Integer(), nullable=True),
        sa.Column("owner_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
        sa.Column("source_quote", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["matter_id"],
            ["matters.id"],
            name=op.f("fk_matter_obligations_matter_id_matters"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_matter_obligations_document_id_documents"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_matter_obligations")),
    )
    op.create_index(
        op.f("ix_matter_obligations_matter_id"), "matter_obligations", ["matter_id"], unique=False
    )
    op.create_index(
        op.f("ix_matter_obligations_document_id"),
        "matter_obligations",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_matter_obligations_due_date"), "matter_obligations", ["due_date"], unique=False
    )
    op.create_index(
        op.f("ix_matter_obligations_status"), "matter_obligations", ["status"], unique=False
    )

    op.create_table(
        "ethical_walls",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("matter_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_ethical_walls_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["matter_id"],
            ["matters.id"],
            name=op.f("fk_ethical_walls_matter_id_matters"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_ethical_walls_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_ethical_walls_created_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ethical_walls")),
        sa.UniqueConstraint("matter_id", "user_id", name="uq_ethical_walls_matter_user"),
    )
    op.create_index(
        op.f("ix_ethical_walls_organization_id"), "ethical_walls", ["organization_id"], unique=False
    )
    op.create_index(
        op.f("ix_ethical_walls_matter_id"), "ethical_walls", ["matter_id"], unique=False
    )
    op.create_index(op.f("ix_ethical_walls_user_id"), "ethical_walls", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_table("ethical_walls")
    op.drop_table("matter_obligations")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_index(op.f("ix_users_scim_external_id"))
        batch_op.drop_column("scim_external_id")
        batch_op.drop_column("mfa_secret")
        batch_op.drop_column("mfa_enabled")
