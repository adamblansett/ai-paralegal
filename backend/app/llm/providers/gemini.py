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


class GeminiProvider(LLMProvider):
    """Cloud Google Gemini REST provider using structured JSON output mode."""

    name = "gemini"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def generate_structured(
        self,
        request: LLMRequest,
        schema_cls: type[T],
        *,
        model: str | None = None,
    ) -> tuple[T, str, str, TokenUsage]:
        if not self.settings.gemini_api_key:
            raise ProviderError("GEMINI_API_KEY is not configured.")

        resolved = model or self.settings.gemini_default_model
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{resolved}:generateContent"
            f"?key={self.settings.gemini_api_key}"
        )

        payload = {
            "systemInstruction": {"parts": [{"text": request.system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": request.user_prompt}]}],
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_tokens,
                "responseMimeType": "application/json",
            },
        }

        try:
            async with httpx.AsyncClient(
                timeout=self.settings.llm_request_timeout_seconds
            ) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                body = resp.json()
        except httpx.HTTPError as exc:
            raise ProviderError(f"Gemini HTTP error ({resolved}): {exc}") from exc

        candidates = body.get("candidates") or []
        if not candidates:
            raise ProviderError(f"Gemini returned no candidates for model {resolved}.")

        parts = candidates[0].get("content", {}).get("parts") or []
        raw_text = "".join(str(p.get("text", "")) for p in parts).strip()
        if not raw_text:
            raise ProviderError(f"Gemini returned empty text for model {resolved}.")

        try:
            parsed = parse_structured_output(raw_text, schema_cls)
        except StructuredOutputError as exc:
            raise ProviderError(f"Gemini structured output error ({resolved}): {exc}") from exc

        usage_meta = body.get("usageMetadata") or {}
        prompt_tokens = int(usage_meta.get("promptTokenCount") or 0)
        completion_tokens = int(usage_meta.get("candidatesTokenCount") or 0)
        total_tokens = int(usage_meta.get("totalTokenCount") or (prompt_tokens + completion_tokens))
        usage = TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )
        return parsed, resolved, raw_text, usage

    async def stream_text(
        self,
        request: LLMRequest,
        *,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream incremental text chunks via Gemini `:streamGenerateContent?alt=sse`."""
        if not self.settings.gemini_api_key:
            raise ProviderError("GEMINI_API_KEY is not configured.")

        resolved = model or self.settings.gemini_default_model
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{resolved}:streamGenerateContent?alt=sse&key={self.settings.gemini_api_key}"
        )
        payload = {
            "systemInstruction": {"parts": [{"text": request.system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": request.user_prompt}]}],
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_tokens,
            },
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
                    stripped = line.strip()
                    if not stripped.startswith("data:"):
                        continue
                    raw_json = stripped[5:].strip()
                    if not raw_json or raw_json == "[DONE]":
                        continue
                    chunk = json.loads(raw_json)
                    candidates = chunk.get("candidates") or []
                    if not candidates:
                        continue
                    parts = candidates[0].get("content", {}).get("parts") or []
                    for part in parts:
                        delta = str(part.get("text") or "")
                        if delta:
                            yield delta
        except httpx.HTTPError as exc:
            raise ProviderError(f"Gemini streaming HTTP error ({resolved}): {exc}") from exc
