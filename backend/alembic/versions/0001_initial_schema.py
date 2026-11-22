"""Initial schema for core matters, documents, chunks, tasks, findings, and audit_logs tables.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-22 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.db.types import EmbeddingVector

revision: str = "0001_initial_schema"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB_COMPAT = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "matters",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("matter_number", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("client_name", sa.String(length=255), nullable=False),
        sa.Column("practice_area", sa.String(length=120), nullable=False),
        sa.Column("jurisdiction", sa.String(length=120), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("metadata_json", JSONB_COMPAT, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_matters")),
    )
    op.create_index(op.f("ix_matters_matter_number"), "matters", ["matter_number"], unique=True)
    op.create_index(op.f("ix_matters_status"), "matters", ["status"], unique=False)

    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("matter_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("document_type", sa.String(length=64), nullable=False),
        sa.Column("storage_backend", sa.String(length=32), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("sha256_hash", sa.String(length=64), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False),
        sa.Column("ingestion_status", sa.String(length=32), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("metadata_json", JSONB_COMPAT, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["matter_id"],
            ["matters.id"],
            name=op.f("fk_documents_matter_id_matters"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
    )
    op.create_index(op.f("ix_documents_matter_id"), "documents", ["matter_id"], unique=False)
    op.create_index(op.f("ix_documents_sha256_hash"), "documents", ["sha256_hash"], unique=False)
    op.create_index(
        op.f("ix_documents_ingestion_status"), "documents", ["ingestion_status"], unique=False
    )

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("matter_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("paragraph_index", sa.Integer(), nullable=True),
        sa.Column("section_heading", sa.String(length=255), nullable=True),
        sa.Column("char_start", sa.Integer(), nullable=True),
        sa.Column("char_end", sa.Integer(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding", EmbeddingVector(384), nullable=True),
        sa.Column("metadata_json", JSONB_COMPAT, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_document_chunks_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["matter_id"],
            ["matters.id"],
            name=op.f("fk_document_chunks_matter_id_matters"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_chunks")),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_document_chunks_doc_idx"),
    )
    op.create_index(
        op.f("ix_document_chunks_document_id"), "document_chunks", ["document_id"], unique=False
    )
    op.create_index(
        op.f("ix_document_chunks_matter_id"), "document_chunks", ["matter_id"], unique=False
    )
    op.create_index(
        "ix_document_chunks_matter_doc",
        "document_chunks",
        ["matter_id", "document_id"],
        unique=False,
    )

    if is_postgres:
        op.execute(
            "CREATE INDEX ix_document_chunks_fts ON document_chunks "
            "USING gin (to_tsvector('english', content))"
        )
        op.execute(
            "CREATE INDEX ix_document_chunks_embedding_hnsw ON document_chunks "
            "USING hnsw (embedding vector_cosine_ops)"
        )

    op.create_table(
        "review_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("matter_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("celery_task_id", sa.String(length=128), nullable=True),
        sa.Column("requested_provider", sa.String(length=64), nullable=True),
        sa.Column("requested_model", sa.String(length=128), nullable=True),
        sa.Column("resolved_provider", sa.String(length=64), nullable=True),
        sa.Column("resolved_model", sa.String(length=128), nullable=True),
        sa.Column("input_params_json", JSONB_COMPAT, nullable=False),
        sa.Column("result_json", JSONB_COMPAT, nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_review_tasks_document_id_documents"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["matter_id"],
            ["matters.id"],
            name=op.f("fk_review_tasks_matter_id_matters"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_tasks")),
    )
    op.create_index(op.f("ix_review_tasks_matter_id"), "review_tasks", ["matter_id"], unique=False)
    op.create_index(
        op.f("ix_review_tasks_document_id"), "review_tasks", ["document_id"], unique=False
    )
    op.create_index(op.f("ix_review_tasks_task_type"), "review_tasks", ["task_type"], unique=False)
    op.create_index(op.f("ix_review_tasks_status"), "review_tasks", ["status"], unique=False)

    op.create_table(
        "extracted_findings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("matter_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("review_task_id", sa.Uuid(), nullable=True),
        sa.Column("finding_type", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("edited_summary", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("grounding_score", sa.Float(), nullable=False),
        sa.Column("requires_escalation", sa.Boolean(), nullable=False),
        sa.Column("escalation_reason", sa.Text(), nullable=True),
        sa.Column("recommended_action", sa.Text(), nullable=True),
        sa.Column("citations_json", JSONB_COMPAT, nullable=False),
        sa.Column("metadata_json", JSONB_COMPAT, nullable=False),
        sa.Column("review_status", sa.String(length=32), nullable=False),
        sa.Column("reviewer_name", sa.String(length=128), nullable=True),
        sa.Column("reviewer_notes", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_extracted_findings_document_id_documents"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["matter_id"],
            ["matters.id"],
            name=op.f("fk_extracted_findings_matter_id_matters"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["review_task_id"],
            ["review_tasks.id"],
            name=op.f("fk_extracted_findings_review_task_id_review_tasks"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_extracted_findings")),
    )
    op.create_index(
        op.f("ix_extracted_findings_matter_id"),
        "extracted_findings",
        ["matter_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_extracted_findings_document_id"),
        "extracted_findings",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_extracted_findings_review_task_id"),
        "extracted_findings",
        ["review_task_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_extracted_findings_finding_type"),
        "extracted_findings",
        ["finding_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_extracted_findings_category"),
        "extracted_findings",
        ["category"],
        unique=False,
    )
    op.create_index(
        op.f("ix_extracted_findings_review_status"),
        "extracted_findings",
        ["review_status"],
        unique=False,
    )
    op.create_index(
        "ix_extracted_findings_matter_status",
        "extracted_findings",
        ["matter_id", "review_status"],
        unique=False,
    )
    op.create_index(
        "ix_extracted_findings_matter_severity",
        "extracted_findings",
        ["matter_id", "severity"],
        unique=False,
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("matter_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("review_task_id", sa.Uuid(), nullable=True),
        sa.Column("finding_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model_name", sa.String(length=128), nullable=True),
        sa.Column("prompt_template", sa.String(length=128), nullable=True),
        sa.Column("prompt_version", sa.String(length=32), nullable=True),
        sa.Column("prompt_text", sa.Text(), nullable=True),
        sa.Column("retrieved_spans_json", JSONB_COMPAT, nullable=False),
        sa.Column("response_metadata_json", JSONB_COMPAT, nullable=False),
        sa.Column("token_usage_json", JSONB_COMPAT, nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("grounding_score", sa.Float(), nullable=True),
        sa.Column("escalated", sa.Boolean(), nullable=False),
        sa.Column("previous_state", sa.String(length=64), nullable=True),
        sa.Column("new_state", sa.String(length=64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_audit_logs_document_id_documents"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["finding_id"],
            ["extracted_findings.id"],
            name=op.f("fk_audit_logs_finding_id_extracted_findings"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["matter_id"],
            ["matters.id"],
            name=op.f("fk_audit_logs_matter_id_matters"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["review_task_id"],
            ["review_tasks.id"],
            name=op.f("fk_audit_logs_review_task_id_review_tasks"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(op.f("ix_audit_logs_matter_id"), "audit_logs", ["matter_id"], unique=False)
    op.create_index(op.f("ix_audit_logs_document_id"), "audit_logs", ["document_id"], unique=False)
    op.create_index(
        op.f("ix_audit_logs_review_task_id"), "audit_logs", ["review_task_id"], unique=False
    )
    op.create_index(op.f("ix_audit_logs_finding_id"), "audit_logs", ["finding_id"], unique=False)
    op.create_index(op.f("ix_audit_logs_action"), "audit_logs", ["action"], unique=False)
    op.create_index(op.f("ix_audit_logs_created_at"), "audit_logs", ["created_at"], unique=False)
    op.create_index(
        "ix_audit_logs_matter_created", "audit_logs", ["matter_id", "created_at"], unique=False
    )


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    op.drop_index("ix_audit_logs_matter_created", table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_created_at"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_action"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_finding_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_review_task_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_document_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_matter_id"), table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index("ix_extracted_findings_matter_severity", table_name="extracted_findings")
    op.drop_index("ix_extracted_findings_matter_status", table_name="extracted_findings")
    op.drop_index(op.f("ix_extracted_findings_review_status"), table_name="extracted_findings")
    op.drop_index(op.f("ix_extracted_findings_category"), table_name="extracted_findings")
    op.drop_index(op.f("ix_extracted_findings_finding_type"), table_name="extracted_findings")
    op.drop_index(op.f("ix_extracted_findings_review_task_id"), table_name="extracted_findings")
    op.drop_index(op.f("ix_extracted_findings_document_id"), table_name="extracted_findings")
    op.drop_index(op.f("ix_extracted_findings_matter_id"), table_name="extracted_findings")
    op.drop_table("extracted_findings")

    op.drop_index(op.f("ix_review_tasks_status"), table_name="review_tasks")
    op.drop_index(op.f("ix_review_tasks_task_type"), table_name="review_tasks")
    op.drop_index(op.f("ix_review_tasks_document_id"), table_name="review_tasks")
    op.drop_index(op.f("ix_review_tasks_matter_id"), table_name="review_tasks")
    op.drop_table("review_tasks")

    if is_postgres:
        op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding_hnsw")
        op.execute("DROP INDEX IF EXISTS ix_document_chunks_fts")

    op.drop_index("ix_document_chunks_matter_doc", table_name="document_chunks")
    op.drop_index(op.f("ix_document_chunks_matter_id"), table_name="document_chunks")
    op.drop_index(op.f("ix_document_chunks_document_id"), table_name="document_chunks")
    op.drop_table("document_chunks")

    op.drop_index(op.f("ix_documents_ingestion_status"), table_name="documents")
    op.drop_index(op.f("ix_documents_sha256_hash"), table_name="documents")
    op.drop_index(op.f("ix_documents_matter_id"), table_name="documents")
    op.drop_table("documents")

    op.drop_index(op.f("ix_matters_status"), table_name="matters")
    op.drop_index(op.f("ix_matters_matter_number"), table_name="matters")
    op.drop_table("matters")
