from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "development"
    app_name: str = "AI Paralegal"
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # Database
    database_url: str = "sqlite+aiosqlite:///./ai_paralegal.db"

    # Redis & Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    task_execution_mode: Literal["celery", "inline", "background"] = "inline"

    # Object / File Storage
    storage_backend: Literal["local", "s3"] = "local"
    local_storage_dir: str = "./storage"
    s3_bucket: str = ""
    s3_endpoint_url: str = ""
    s3_region: str = "us-east-1"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""

    # LLM Routing & Grounding Controls
    llm_default_provider: Literal["mock", "ollama", "gemini"] = "mock"
    llm_fallback_providers: str = "ollama,gemini,mock"
    llm_confidence_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    llm_grounding_threshold: float = Field(default=0.70, ge=0.0, le=1.0)
    llm_escalate_high_risk: bool = True
    llm_request_timeout_seconds: float = 90.0

    # Ollama Local Open-Weight Models
    ollama_base_url: str = "http://localhost:11434"
    ollama_default_model: str = "llama-3.1-8b-instruct"
    ollama_embedding_model: str = "nomic-embed-text"

    # Cloud Gemini Models
    gemini_api_key: str = ""
    gemini_default_model: str = "gemini-2.5-flash"
    gemini_embedding_model: str = "text-embedding-004"

    # Embeddings
    embedding_provider: Literal["deterministic", "ollama", "gemini"] = "deterministic"
    embedding_dimensions: int = Field(default=384, ge=32, le=4096)

    # Authentication & RBAC
    auth_secret_key: str = "dev-insecure-secret-change-in-production-32bytes"
    auth_token_expire_minutes: int = Field(default=480, ge=5, le=43200)
    auth_auto_seed_demo_users: bool = True

    # OCR / Scanned PDF Pipeline
    ocr_backend: Literal["auto", "tesseract", "mock", "disabled"] = "auto"
    ocr_min_chars_per_page: int = Field(default=20, ge=1, le=500)

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def fallback_provider_list(self) -> list[str]:
        return [
            item.strip().lower() for item in self.llm_fallback_providers.split(",") if item.strip()
        ]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()


def clear_settings_cache() -> None:
    """Clear cached settings (primarily for testing overrides)."""
    get_settings.cache_clear()
