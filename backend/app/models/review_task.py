import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import JSONBCompat

if TYPE_CHECKING:
    from app.models.finding import ExtractedFinding
    from app.models.matter import Matter


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ReviewTask(Base):
    """Tracks asynchronous ingestion, contract review, discovery synthesis, and drafting jobs."""

    __tablename__ = "review_tasks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    matter_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("matters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    task_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    requested_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolved_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolved_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_params_json: Mapped[dict[str, Any]] = mapped_column(
        JSONBCompat, nullable=False, default=dict
    )
    result_json: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    matter: Mapped["Matter"] = relationship(back_populates="review_tasks")
    findings: Mapped[list["ExtractedFinding"]] = relationship(back_populates="review_task")
