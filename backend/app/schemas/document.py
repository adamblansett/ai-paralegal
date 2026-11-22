import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


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
