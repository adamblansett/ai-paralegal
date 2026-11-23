import json
import time
from typing import TypeVar

from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.core.metrics import metrics_registry
from app.core.telemetry import get_tracer
from app.llm.providers import (
    OLLAMA_MODEL_ALIASES,
    GeminiProvider,
    LLMProvider,
    MockLLMProvider,
    OllamaProvider,
    ProviderError,
)
from app.llm.types import LLMRequest, LLMResponse
from app.services.guardrails import CLOUD_RESTRICTED_PRIVILEGES, redact_pii_for_cloud

T = TypeVar("T", bound=BaseModel)

CLOUD_PROVIDERS: frozenset[str] = frozenset({"gemini"})

DEFAULT_TASK_MODELS: dict[str, dict[str, str]] = {
    "contract_review": {
        "ollama": "qwen-2.5-7b-instruct",
        "gemini": "gemini-2.5-flash",
    },
    "discovery_synthesis": {
        "ollama": "mistral-nemo-12b",
        "gemini": "gemini-2.5-pro",
    },
    "drafting": {
        "ollama": "llama-3.1-8b-instruct",
        "gemini": "gemini-2.5-flash",
    },
    "qa_research": {
        "ollama": "qwen-2.5-7b-instruct",
        "gemini": "gemini-2.5-flash",
    },
}


class ModelRouter:
    """Provider-agnostic LLM router with guardrails, per-task routing, and fallbacks."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._providers: dict[str, LLMProvider] = {
            "ollama": OllamaProvider(self.settings),
            "gemini": GeminiProvider(self.settings),
            "mock": MockLLMProvider(),
        }

    def get_provider(self, name: str) -> LLMProvider:
        key = name.strip().lower()
        if key not in self._providers:
            raise ValueError(f"Unsupported LLM provider: {name}")
        return self._providers[key]

    def supported_models(self) -> dict[str, list[str]]:
        return {
            "ollama": list(OLLAMA_MODEL_ALIASES.keys()),
            "gemini": [self.settings.gemini_default_model, "gemini-2.5-pro"],
            "mock": ["mock-legal-deterministic-v1"],
        }

    def resolve_task_model(
        self,
        provider: str,
        task_kind: str,
        requested_model: str | None,
    ) -> str | None:
        """Select explicit model override, env-configured per-task default, or built-in default."""
        if requested_model and requested_model.strip():
            return requested_model.strip()

        key = provider.strip().lower()
        if key == "mock":
            return None

        raw_override = self.settings.llm_task_routing_json.strip()
        if raw_override:
            try:
                parsed = json.loads(raw_override)
                if isinstance(parsed, dict):
                    task_cfg = parsed.get(task_kind)
                    if isinstance(task_cfg, dict) and isinstance(task_cfg.get(key), str):
                        return str(task_cfg[key])
            except Exception:
                pass

        return DEFAULT_TASK_MODELS.get(task_kind, {}).get(key)

    def _build_candidate_chain(
        self,
        *,
        provider: str | None,
        enable_fallback: bool,
    ) -> list[str]:
        primary = (provider or self.settings.llm_default_provider).strip().lower()
        chain = [primary]
        if enable_fallback:
            for candidate in self.settings.fallback_provider_list:
                if candidate in self._providers and candidate not in chain:
                    chain.append(candidate)
        return chain

    def _prepare_cloud_request(
        self,
        provider_name: str,
        request: LLMRequest,
    ) -> tuple[LLMRequest, dict[str, int]]:
        """Enforce privilege blocking & PII redaction before external cloud transmission."""
        if provider_name not in CLOUD_PROVIDERS:
            return request, {}

        if self.settings.llm_block_privileged_for_cloud:
            privileged_docs = sorted(
                {
                    c.document_filename
                    for c in request.context_chunks
                    if c.privilege_status in CLOUD_RESTRICTED_PRIVILEGES
                }
            )
            if privileged_docs:
                raise ProviderError(
                    f"Outbound guardrail blocked cloud provider '{provider_name}' because "
                    f"source chunks contain privileged material ({', '.join(privileged_docs[:3])})."
                )

        if not self.settings.llm_redact_pii_for_cloud:
            return request, {}

        redacted_prompt, counts = redact_pii_for_cloud(request.user_prompt)
        redacted_chunks = []
        for chunk in request.context_chunks:
            red_content, chunk_counts = redact_pii_for_cloud(chunk.content)
            for k, v in chunk_counts.items():
                # Prompt usually echoes chunk text, so track chunk redactions if prompt skipped them
                if k not in counts:
                    counts[k] = counts.get(k, 0) + v
            redacted_chunks.append(chunk.model_copy(update={"content": red_content}))

        if counts:
            metrics_registry.record_pii_redaction(provider_name, counts)

        return (
            request.model_copy(
                update={
                    "user_prompt": redacted_prompt,
                    "context_chunks": redacted_chunks,
                }
            ),
            counts,
        )

    async def generate_structured(
        self,
        request: LLMRequest,
        schema_cls: type[T],
        *,
        provider: str | None = None,
        model: str | None = None,
        enable_fallback: bool = True,
    ) -> LLMResponse[T]:
        """Route a structured generation request across primary and fallback providers."""
        candidates = self._build_candidate_chain(provider=provider, enable_fallback=enable_fallback)
        errors: list[str] = []
        tracer = get_tracer("ai_paralegal.llm")

        with tracer.start_as_current_span("llm.generate_structured") as span:
            span.set_attribute("llm.task_kind", request.task_kind)
            span.set_attribute(
                "llm.requested_provider", provider or self.settings.llm_default_provider
            )

            for idx, candidate_name in enumerate(candidates):
                prov = self.get_provider(candidate_name)
                candidate_model = self.resolve_task_model(
                    candidate_name,
                    request.task_kind,
                    model if idx == 0 else None,
                )
                t0 = time.perf_counter()
                try:
                    effective_request, redaction_summary = self._prepare_cloud_request(
                        candidate_name, request
                    )
                    parsed, resolved_model, raw_text, usage = await prov.generate_structured(
                        effective_request,
                        schema_cls,
                        model=candidate_model,
                    )
                    latency_ms = int((time.perf_counter() - t0) * 1000)
                    metrics_registry.record_llm_call(
                        provider=prov.name,
                        model_name=resolved_model,
                        task_kind=request.task_kind,
                        status="success",
                        latency_ms=latency_ms,
                        prompt_tokens=usage.prompt_tokens,
                        completion_tokens=usage.completion_tokens,
                    )
                    span.set_attribute("llm.provider", prov.name)
                    span.set_attribute("llm.model", resolved_model)
                    span.set_attribute("llm.latency_ms", latency_ms)
                    span.set_attribute("llm.fallback_used", idx > 0)
                    span.set_attribute("llm.pii_redacted", bool(redaction_summary))
                    return LLMResponse[T](
                        provider=prov.name,
                        model_name=resolved_model,
                        data=parsed,
                        raw_text=raw_text,
                        token_usage=usage,
                        latency_ms=latency_ms,
                        fallback_used=idx > 0,
                        fallback_errors=errors,
                        redaction_summary=redaction_summary,
                    )
                except ProviderError as exc:
                    latency_ms = int((time.perf_counter() - t0) * 1000)
                    metrics_registry.record_llm_call(
                        provider=candidate_name,
                        model_name=candidate_model or "default",
                        task_kind=request.task_kind,
                        status="error",
                        latency_ms=latency_ms,
                    )
                    errors.append(f"{candidate_name}: {exc}")
                    continue

        raise ProviderError(
            "All LLM providers failed for structured generation: " + " | ".join(errors)
        )
