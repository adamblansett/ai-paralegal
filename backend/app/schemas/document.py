import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PrivilegeStatus = Literal[
    "none",
    "attorney_client",
    "work_product",
    "confidential",
    "privileged_and_confidential",
]


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    matter_id: uuid.UUID
    filename: str
    content_type: str
    document_type: str
    storage_backend: str
    storage_key: str
    sha256_hash: str
    file_size_bytes: int
    ingestion_status: str
    error_message: str | None
    page_count: int
    word_count: int
    chunk_count: int
    bates_start: str | None = None
    bates_end: str | None = None
    privilege_status: str = "none"
    privilege_reason: str | None = None
    pii_detected_count: int = 0
    pii_summary_json: dict[str, Any] = Field(default_factory=dict)
    legal_hold_active: bool = False
    retention_until: datetime | None = None
    metadata_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class DocumentChunkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    matter_id: uuid.UUID
    chunk_index: int
    content: str
    page_number: int | None
    paragraph_index: int | None
    section_heading: str | None
    char_start: int | None
    char_end: int | None
    token_count: int
    metadata_json: dict[str, Any]
    created_at: datetime


class DocumentUploadResponse(BaseModel):
    document: DocumentRead
    task_id: uuid.UUID
    ingestion_status: str


class DocumentPrivilegeUpdate(BaseModel):
    privilege_status: PrivilegeStatus
    privilege_reason: str | None = Field(default=None, max_length=1000)


class BatesStampRequest(BaseModel):
    prefix: str = Field(
        default="PROD",
        min_length=1,
        max_length=24,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    start_number: int = Field(default=1, ge=1, le=9_999_999)
    zero_pad: int = Field(default=6, ge=4, le=10)
    document_ids: list[uuid.UUID] | None = None


class BatesStampResponse(BaseModel):
    matter_id: uuid.UUID
    prefix: str
    stamped_count: int
    next_number: int
    documents: list[DocumentRead]
