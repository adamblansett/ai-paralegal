import json
from collections.abc import AsyncIterator
from typing import TypeVar

import httpx
from pydantic import BaseModel

from app.core.config import Settings
from app.llm.json_utils import StructuredOutputError, parse_structured_output
from app.llm.providers.base import LLMProvider, ProviderError
from app.llm.types import LLMRequest, TokenUsage

T = TypeVar("T", bound=BaseModel)

OLLAMA_MODEL_ALIASES: dict[str, str] = {
    "llama-3.1-8b-instruct": "llama3.1:8b-instruct-q4_K_M",
    "qwen-2.5-7b-instruct": "qwen2.5:7b-instruct",
    "mistral-nemo-12b": "mistral-nemo:12b",
    "deepseek-r1-distill-qwen-8b": "deepseek-r1:8b",
}


class OllamaProvider(LLMProvider):
    """Local Ollama provider supporting open-weight models via `/api/chat`."""

    name = "ollama"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @staticmethod
    def resolve_model_tag(model_name: str) -> str:
        return OLLAMA_MODEL_ALIASES.get(model_name, model_name)

    async def generate_structured(
        self,
        request: LLMRequest,
        schema_cls: type[T],
        *,
        model: str | None = None,
    ) -> tuple[T, str, str, TokenUsage]:
        requested = model or self.settings.ollama_default_model
        resolved = self.resolve_model_tag(requested)
        url = f"{self.settings.ollama_base_url.rstrip('/')}/api/chat"

        payload = {
            "model": resolved,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": request.temperature,
                "num_predict": request.max_tokens,
            },
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
        }

        try:
            async with httpx.AsyncClient(
                timeout=self.settings.llm_request_timeout_seconds
            ) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                body = resp.json()
        except httpx.HTTPError as exc:
            raise ProviderError(f"Ollama HTTP error ({resolved}): {exc}") from exc

        raw_text = str(body.get("message", {}).get("content", "")).strip()
        if not raw_text:
            raise ProviderError(f"Ollama returned empty message content for model {resolved}.")

        try:
            parsed = parse_structured_output(raw_text, schema_cls)
        except StructuredOutputError as exc:
            raise ProviderError(f"Ollama structured output error ({resolved}): {exc}") from exc

        prompt_tokens = int(body.get("prompt_eval_count") or 0)
        completion_tokens = int(body.get("eval_count") or 0)
        usage = TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )
        return parsed, requested, raw_text, usage

    async def stream_text(
        self,
        request: LLMRequest,
        *,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream tokens incrementally from Ollama `/api/chat` with `stream=True`."""
        requested = model or self.settings.ollama_default_model
        resolved = self.resolve_model_tag(requested)
        url = f"{self.settings.ollama_base_url.rstrip('/')}/api/chat"
        payload = {
            "model": resolved,
            "stream": True,
            "options": {
                "temperature": request.temperature,
                "num_predict": request.max_tokens,
            },
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
        }
        try:
            async with (
                httpx.AsyncClient(
                    timeout=self.settings.llm_request_timeout_seconds
                ) as client,
                client.stream("POST", url, json=payload) as resp,
            ):
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    delta = str(chunk.get("message", {}).get("content") or "")
                    if delta:
                        yield delta
                    if chunk.get("done"):
                        break
        except httpx.HTTPError as exc:
            raise ProviderError(f"Ollama streaming HTTP error ({resolved}): {exc}") from exc
