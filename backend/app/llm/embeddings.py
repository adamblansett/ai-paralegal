import hashlib
import math
import re
from collections.abc import Sequence

import httpx

from app.core.config import Settings, get_settings

_TOKEN_RE = re.compile(r"[a-z0-9]{2,}")


def cosine_similarity(vec_a: Sequence[float] | None, vec_b: Sequence[float] | None) -> float:
    """Compute cosine similarity between two equal-length float vectors."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=True))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (norm_a * norm_b)))


def _normalize_dimensions(raw: list[float], dimensions: int) -> list[float]:
    trimmed = raw[:dimensions] if len(raw) >= dimensions else raw + [0.0] * (dimensions - len(raw))
    norm = math.sqrt(sum(v * v for v in trimmed))
    if norm == 0.0:
        return trimmed
    return [v / norm for v in trimmed]


def deterministic_embed(text: str, dimensions: int = 384) -> list[float]:
    """Generate a normalized deterministic embedding using signed feature hashing.

    Combines word unigrams, word bigrams, and character 3-grams so offline hybrid
    retrieval preserves strong lexical and morphological similarity without external APIs.
    """
    vec = [0.0] * dimensions
    lowered = text.lower().strip()
    if not lowered:
        return vec

    tokens = _TOKEN_RE.findall(lowered)
    features: list[tuple[str, float]] = []

    for tok in tokens:
        features.append((f"w:{tok}", 1.5))
    for i in range(len(tokens) - 1):
        features.append((f"bi:{tokens[i]}_{tokens[i + 1]}", 1.2))
    compact = " ".join(tokens)
    for i in range(max(0, len(compact) - 2)):
        features.append((f"c3:{compact[i : i + 3]}", 0.35))

    for feat, weight in features:
        digest = hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(digest[:4], "little") % dimensions
        sign = 1.0 if (digest[4] & 1) == 0 else -1.0
        vec[idx] += sign * weight

    return _normalize_dimensions(vec, dimensions)


class EmbeddingService:
    """Embedding generator supporting deterministic offline, Ollama, and Gemini providers."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def embed_text(self, text: str) -> list[float]:
        provider = self.settings.embedding_provider
        dims = self.settings.embedding_dimensions
        if provider == "ollama":
            try:
                return await self._embed_ollama(text, dims)
            except Exception:
                return deterministic_embed(text, dims)
        if provider == "gemini" and self.settings.gemini_api_key:
            try:
                return await self._embed_gemini(text, dims)
            except Exception:
                return deterministic_embed(text, dims)
        return deterministic_embed(text, dims)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if self.settings.embedding_provider == "deterministic":
            dims = self.settings.embedding_dimensions
            return [deterministic_embed(t, dims) for t in texts]
        results: list[list[float]] = []
        for item in texts:
            results.append(await self.embed_text(item))
        return results

    async def _embed_ollama(self, text: str, dimensions: int) -> list[float]:
        url = f"{self.settings.ollama_base_url.rstrip('/')}/api/embeddings"
        payload = {"model": self.settings.ollama_embedding_model, "prompt": text}
        async with httpx.AsyncClient(timeout=self.settings.llm_request_timeout_seconds) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        raw = [float(x) for x in data["embedding"]]
        return _normalize_dimensions(raw, dimensions)

    async def _embed_gemini(self, text: str, dimensions: int) -> list[float]:
        model = self.settings.gemini_embedding_model
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent"
            f"?key={self.settings.gemini_api_key}"
        )
        payload = {"content": {"parts": [{"text": text}]}}
        async with httpx.AsyncClient(timeout=self.settings.llm_request_timeout_seconds) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        raw = [float(x) for x in data["embedding"]["values"]]
        return _normalize_dimensions(raw, dimensions)
