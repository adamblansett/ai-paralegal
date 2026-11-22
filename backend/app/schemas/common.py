from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    database: str
    app_name: str
    environment: str
    default_provider: str
    fallback_providers: list[str]
    supported_models: dict[str, list[str]]
    storage_backend: str
    task_execution_mode: str
    auth_enabled: bool = True
    ocr_backend: str = "auto"
    tesseract_available: bool = False
    disclaimer: str


class MessageResponse(BaseModel):
    detail: str
    disclaimer: str | None = Field(default=None)
