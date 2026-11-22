from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel

from app.llm.types import LLMRequest, TokenUsage

T = TypeVar("T", bound=BaseModel)


class ProviderError(RuntimeError):
    """Raised when an LLM provider fails or returns invalid structured output."""


class LLMProvider(ABC):
    """Abstract provider interface for structured legal LLM generation."""

    name: str

    @abstractmethod
    async def generate_structured(
        self,
        request: LLMRequest,
        schema_cls: type[T],
        *,
        model: str | None = None,
    ) -> tuple[T, str, str, TokenUsage]:
        """Execute structured generation.

        Returns:
            `(parsed_model, resolved_model_name, raw_text, token_usage)`
        """
