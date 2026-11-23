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
    auth_refresh_token_expire_days: int = Field(default=14, ge=1, le=90)
    auth_auto_seed_demo_users: bool = True
    oidc_enabled: bool = False
    oidc_issuer_url: str = ""
    oidc_client_id: str = ""
    oidc_audience: str = ""
    oidc_role_claim: str = "groups"
    oidc_role_mapping_json: str = (
        '{"legal-partners":"partner","legal-associates":"associate",'
        '"legal-paralegals":"paralegal","legal-auditors":"auditor"}'
    )
    default_organization_slug: str = "default-firm"
    default_organization_name: str = "Meridian Legal Group LLP"

    # Storage Security
    s3_server_side_encryption: str = "AES256"
    s3_kms_key_id: str = ""

    # Upload Hardening, Webhooks & Outbound Cloud Guardrails
    max_upload_size_mb: int = Field(default=25, ge=1, le=500)
    allowed_upload_extensions: str = ".pdf,.docx,.txt,.md,.eml,.vtt,.srt"
    llm_redact_pii_for_cloud: bool = True
    llm_block_privileged_for_cloud: bool = True
    llm_task_routing_json: str = ""
    webhook_allow_private_ips: bool = False

    # Distributed Backplanes, Rate Limiting & Observability
    rate_limit_enabled: bool = True
    rate_limit_backend: Literal["memory", "redis", "auto"] = "memory"
    rate_limit_auth_per_minute: int = Field(default=120, ge=1, le=10000)
    rate_limit_llm_per_minute: int = Field(default=120, ge=1, le=10000)
    event_bus_backend: Literal["memory", "redis", "auto"] = "memory"
    otel_enabled: bool = False
    otel_service_name: str = "ai-paralegal-api"
    otel_exporter_otlp_endpoint: str = ""
    otel_sample_ratio: float = Field(default=1.0, ge=0.0, le=1.0)

    # Automated Data Retention & Compliance Sweeps
    retention_sweep_enabled: bool = True
    retention_sweep_interval_hours: int = Field(default=24, ge=1, le=720)

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

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def allowed_upload_extension_set(self) -> set[str]:
        return {
            ext.strip().lower()
            for ext in self.allowed_upload_extensions.split(",")
            if ext.strip()
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()


def clear_settings_cache() -> None:
    """Clear cached settings (primarily for testing overrides)."""
    get_settings.cache_clear()


_DEFAULT_DEV_SECRET = "dev-insecure-secret-change-in-production-32bytes"


def validate_production_security(settings: Settings) -> None:
    """Fail fast at boot if insecure dev defaults remain active in production."""
    if settings.app_env.strip().lower() != "production":
        return

    errors: list[str] = []
    if settings.auth_secret_key == _DEFAULT_DEV_SECRET or len(settings.auth_secret_key) < 32:
        errors.append(
            "AUTH_SECRET_KEY must be overridden with a unique >=32-byte secret in production."
        )
    if settings.auth_auto_seed_demo_users:
        errors.append("AUTH_AUTO_SEED_DEMO_USERS must be false in production.")
    if "*" in settings.cors_origin_list:
        errors.append("CORS_ORIGINS must not contain wildcard '*' in production.")
    if errors:
        raise RuntimeError("Production security validation failed: " + " | ".join(errors))
