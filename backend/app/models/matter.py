import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import JSONBCompat

if TYPE_CHECKING:
    from app.models.audit_log import AuditLog
    from app.models.document import Document
    from app.models.finding import ExtractedFinding
    from app.models.review_task import ReviewTask
    from app.models.user import MatterMembership


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Matter(Base):
    """A legal matter or case grouping documents, analyses, findings, and audit logs."""

    __tablename__ = "matters"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    matter_number: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    client_name: Mapped[str] = mapped_column(String(255), nullable=False)
    practice_area: Mapped[str] = mapped_column(String(120), nullable=False, default="Corporate")
    jurisdiction: Mapped[str | None] = mapped_column(String(120), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONBCompat, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    documents: Mapped[list["Document"]] = relationship(
        back_populates="matter", cascade="all, delete-orphan"
    )
    review_tasks: Mapped[list["ReviewTask"]] = relationship(
        back_populates="matter", cascade="all, delete-orphan"
    )
    findings: Mapped[list["ExtractedFinding"]] = relationship(
        back_populates="matter", cascade="all, delete-orphan"
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        back_populates="matter", cascade="all, delete-orphan"
    )
    memberships: Mapped[list["MatterMembership"]] = relationship(
        back_populates="matter", cascade="all, delete-orphan"
    )
