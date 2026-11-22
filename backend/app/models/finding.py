import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import JSONBCompat

if TYPE_CHECKING:
    from app.models.matter import Matter
    from app.models.review_task import ReviewTask


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ExtractedFinding(Base):
    """Citation-grounded legal finding awaiting or having completed attorney review."""

    __tablename__ = "extracted_findings"
    __table_args__ = (
        Index("ix_extracted_findings_matter_status", "matter_id", "review_status"),
        Index("ix_extracted_findings_matter_severity", "matter_id", "severity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    matter_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("matters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    review_task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("review_tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    finding_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    edited_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[str] = mapped_column(String(32), nullable=False, default="medium")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    grounding_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    requires_escalation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    escalation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    citations_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONBCompat, nullable=False, default=list
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    review_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending_review", index=True
    )
    reviewer_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reviewer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    matter: Mapped["Matter"] = relationship(back_populates="findings")
    review_task: Mapped["ReviewTask | None"] = relationship(back_populates="findings")
