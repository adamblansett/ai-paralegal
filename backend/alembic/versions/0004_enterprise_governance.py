"""Add organizations, webhook_subscriptions, legal-hold/retention columns, and audit hash chain.

Revision ID: 0004_enterprise_governance
Revises: 0003_privilege_bates_tokens
Create Date: 2026-11-22 13:15:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004_enterprise_governance"
down_revision: str | Sequence[str] | None = "0003_privilege_bates_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB_COMPAT = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=False, server_default="2555"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organizations")),
    )
    op.create_index(op.f("ix_organizations_slug"), "organizations", ["slug"], unique=True)

    op.create_table(
        "webhook_subscriptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("matter_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("channel_type", sa.String(length=32), nullable=False, server_default="webhook"),
        sa.Column("target_url", sa.String(length=1024), nullable=False),
        sa.Column("signing_secret", sa.String(length=128), nullable=True),
        sa.Column("events_json", JSONB_COMPAT, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["matter_id"],
            ["matters.id"],
            name=op.f("fk_webhook_subscriptions_matter_id_matters"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_webhook_subscriptions_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_subscriptions")),
    )
    op.create_index(
        op.f("ix_webhook_subscriptions_matter_id"),
        "webhook_subscriptions",
        ["matter_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_webhook_subscriptions_organization_id"),
        "webhook_subscriptions",
        ["organization_id"],
        unique=False,
    )

    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("organization_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            op.f("fk_users_organization_id_organizations"),
            "organizations",
            ["organization_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(op.f("ix_users_organization_id"), ["organization_id"], unique=False)

    with op.batch_alter_table("playbooks") as batch_op:
        batch_op.add_column(sa.Column("organization_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            op.f("fk_playbooks_organization_id_organizations"),
            "organizations",
            ["organization_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            op.f("ix_playbooks_organization_id"), ["organization_id"], unique=False
        )

    with op.batch_alter_table("matters") as batch_op:
        batch_op.add_column(sa.Column("organization_id", sa.Uuid(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "legal_hold_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(sa.Column("legal_hold_reason", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("legal_hold_set_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("legal_hold_set_by", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("retention_days", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            op.f("fk_matters_organization_id_organizations"),
            "organizations",
            ["organization_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(op.f("ix_matters_organization_id"), ["organization_id"], unique=False)
        batch_op.create_index(
            op.f("ix_matters_legal_hold_active"), ["legal_hold_active"], unique=False
        )

    with op.batch_alter_table("documents") as batch_op:
        batch_op.add_column(
            sa.Column(
                "legal_hold_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_index(
            op.f("ix_documents_legal_hold_active"), ["legal_hold_active"], unique=False
        )

    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.add_column(
            sa.Column("sequence_number", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("previous_hash", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("entry_hash", sa.String(length=64), nullable=True))
        batch_op.create_index(
            op.f("ix_audit_logs_sequence_number"), ["sequence_number"], unique=False
        )
        batch_op.create_index(op.f("ix_audit_logs_entry_hash"), ["entry_hash"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.drop_index(op.f("ix_audit_logs_entry_hash"))
        batch_op.drop_index(op.f("ix_audit_logs_sequence_number"))
        batch_op.drop_column("entry_hash")
        batch_op.drop_column("previous_hash")
        batch_op.drop_column("sequence_number")

    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_index(op.f("ix_documents_legal_hold_active"))
        batch_op.drop_column("retention_until")
        batch_op.drop_column("legal_hold_active")

    with op.batch_alter_table("matters") as batch_op:
        batch_op.drop_index(op.f("ix_matters_legal_hold_active"))
        batch_op.drop_index(op.f("ix_matters_organization_id"))
        batch_op.drop_constraint(
            op.f("fk_matters_organization_id_organizations"), type_="foreignkey"
        )
        batch_op.drop_column("retention_days")
        batch_op.drop_column("legal_hold_set_by")
        batch_op.drop_column("legal_hold_set_at")
        batch_op.drop_column("legal_hold_reason")
        batch_op.drop_column("legal_hold_active")
        batch_op.drop_column("organization_id")

    with op.batch_alter_table("playbooks") as batch_op:
        batch_op.drop_index(op.f("ix_playbooks_organization_id"))
        batch_op.drop_constraint(
            op.f("fk_playbooks_organization_id_organizations"), type_="foreignkey"
        )
        batch_op.drop_column("organization_id")

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_index(op.f("ix_users_organization_id"))
        batch_op.drop_constraint(
            op.f("fk_users_organization_id_organizations"), type_="foreignkey"
        )
        batch_op.drop_column("organization_id")

    op.drop_index(
        op.f("ix_webhook_subscriptions_organization_id"), table_name="webhook_subscriptions"
    )
    op.drop_index(op.f("ix_webhook_subscriptions_matter_id"), table_name="webhook_subscriptions")
    op.drop_table("webhook_subscriptions")

    op.drop_index(op.f("ix_organizations_slug"), table_name="organizations")
    op.drop_table("organizations")
