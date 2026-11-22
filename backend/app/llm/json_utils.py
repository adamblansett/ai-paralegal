import json
import re

from pydantic import BaseModel, ValidationError

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", flags=re.IGNORECASE | re.DOTALL)
_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", flags=re.IGNORECASE | re.MULTILINE)


class StructuredOutputError(ValueError):
    """Raised when an LLM response cannot be parsed into the target schema."""


def strip_reasoning_traces(text: str) -> str:
    """Remove DeepSeek-style `<think>...</think>` reasoning blocks and markdown fences."""
    cleaned = _THINK_BLOCK_RE.sub("", text).strip()
    if "```" in cleaned:
        cleaned = _CODE_FENCE_RE.sub("", cleaned).strip()
    return cleaned


def extract_json_payload(raw_text: str) -> str:
    """Extract the first balanced JSON object or array from raw LLM text."""
    cleaned = strip_reasoning_traces(raw_text)
    if not cleaned:
        raise StructuredOutputError("Empty LLM response; expected JSON payload.")

    # Fast path if already valid JSON
    try:
        json.loads(cleaned)
        return cleaned
    except json.JSONDecodeError:
        pass

    start_candidates = [idx for idx in (cleaned.find("{"), cleaned.find("[")) if idx != -1]
    if not start_candidates:
        raise StructuredOutputError("No JSON object or array delimiter found in LLM output.")

    start = min(start_candidates)
    open_char = cleaned[start]
    close_char = "}" if open_char == "{" else "]"

    depth = 0
    in_string = False
    escaped = False

    for idx in range(start, len(cleaned)):
        ch = cleaned[idx]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                candidate = cleaned[start : idx + 1]
                try:
                    json.loads(candidate)
                    return candidate
                except json.JSONDecodeError as exc:
                    raise StructuredOutputError(f"Malformed JSON segment: {exc}") from exc

    raise StructuredOutputError("Unterminated JSON payload in LLM output.")


def parse_structured_output[T: BaseModel](raw_text: str, schema_cls: type[T]) -> T:
    """Parse and validate raw LLM text into a Pydantic v2 model."""
    json_payload = extract_json_payload(raw_text)
    try:
        return schema_cls.model_validate_json(json_payload)
    except ValidationError as exc:
        raise StructuredOutputError(
            f"LLM output failed schema validation for {schema_cls.__name__}: {exc}"
        ) from exc
