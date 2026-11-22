import time
from typing import TypeVar

from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.llm.providers import (
    OLLAMA_MODEL_ALIASES,
    GeminiProvider,
    LLMProvider,
    MockLLMProvider,
    OllamaProvider,
    ProviderError,
)
from app.llm.types import LLMRequest, LLMResponse

T = TypeVar("T", bound=BaseModel)


class ModelRouter:
    """Provider-agnostic LLM router with ordered fallbacks and structured validation."""

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

        for idx, candidate_name in enumerate(candidates):
            prov = self.get_provider(candidate_name)
            candidate_model = model if idx == 0 else None
            t0 = time.perf_counter()
            try:
                parsed, resolved_model, raw_text, usage = await prov.generate_structured(
                    request,
                    schema_cls,
                    model=candidate_model,
                )
                latency_ms = int((time.perf_counter() - t0) * 1000)
                return LLMResponse[T](
                    provider=prov.name,
                    model_name=resolved_model,
                    data=parsed,
                    raw_text=raw_text,
                    token_usage=usage,
                    latency_ms=latency_ms,
                    fallback_used=idx > 0,
                    fallback_errors=errors,
                )
            except ProviderError as exc:
                errors.append(f"{candidate_name}: {exc}")
                continue

        raise ProviderError(
            "All LLM providers failed for structured generation: " + " | ".join(errors)
        )
