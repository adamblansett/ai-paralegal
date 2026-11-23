import re
import threading
from collections import defaultdict
from typing import Any

_UUID_RE = re.compile(
    r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_NUMERIC_ID_RE = re.compile(r"/\d+(?=/|$)")

# Estimated pricing USD per 1M tokens (approximate list pricing for budget guardrails)
_MODEL_PRICING_PER_1M: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash": (0.15, 0.60),
    "gemini-2.5-pro": (1.25, 5.00),
    "gemini-2.0-flash": (0.10, 0.40),
}


def normalize_metric_path(path: str) -> str:
    """Collapse UUIDs and integer IDs in HTTP paths to prevent cardinality explosion."""
    normalized = _UUID_RE.sub("/{id}", path)
    normalized = _NUMERIC_ID_RE.sub("/{id}", normalized)
    return normalized


def estimate_llm_cost_usd(
    provider: str,
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """Estimate USD cost for an LLM invocation based on provider/model pricing."""
    if provider in {"mock", "ollama"}:
        return 0.0
    in_rate, out_rate = _MODEL_PRICING_PER_1M.get(model_name, (0.30, 1.20))
    return (prompt_tokens / 1_000_000.0) * in_rate + (completion_tokens / 1_000_000.0) * out_rate


def _esc_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


class MetricsRegistry:
    """In-process Prometheus and JSON telemetry registry for AI Paralegal."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._http_requests: dict[tuple[str, str, str], int] = defaultdict(int)
            self._http_duration_sum: dict[tuple[str, str], float] = defaultdict(float)
            self._http_duration_count: dict[tuple[str, str], int] = defaultdict(int)

            self._llm_calls: dict[tuple[str, str, str, str], int] = defaultdict(int)
            self._llm_latency_ms_sum: dict[tuple[str, str, str], int] = defaultdict(int)
            self._llm_latency_count: dict[tuple[str, str, str], int] = defaultdict(int)
            self._llm_tokens: dict[tuple[str, str, str], int] = defaultdict(int)
            self._llm_cost_usd: dict[tuple[str, str], float] = defaultdict(float)

            self._pii_redactions: dict[tuple[str, str], int] = defaultdict(int)
            self._ocr_pages: dict[tuple[str, str], int] = defaultdict(int)
            self._grounding_failures: int = 0
            self._webhook_deliveries: dict[tuple[str, str], int] = defaultdict(int)
            self._retention_purged: int = 0

    def record_http_request(
        self,
        method: str,
        path: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        norm_path = normalize_metric_path(path)
        with self._lock:
            self._http_requests[(method.upper(), norm_path, str(status_code))] += 1
            self._http_duration_sum[(method.upper(), norm_path)] += max(0.0, duration_seconds)
            self._http_duration_count[(method.upper(), norm_path)] += 1

    def record_llm_call(
        self,
        *,
        provider: str,
        model_name: str,
        task_kind: str,
        status: str,
        latency_ms: int,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        cost = estimate_llm_cost_usd(provider, model_name, prompt_tokens, completion_tokens)
        with self._lock:
            self._llm_calls[(provider, model_name, task_kind, status)] += 1
            self._llm_latency_ms_sum[(provider, model_name, task_kind)] += max(0, latency_ms)
            self._llm_latency_count[(provider, model_name, task_kind)] += 1
            self._llm_tokens[(provider, model_name, "prompt")] += max(0, prompt_tokens)
            self._llm_tokens[(provider, model_name, "completion")] += max(0, completion_tokens)
            self._llm_cost_usd[(provider, model_name)] += cost

    def record_pii_redaction(self, provider: str, counts_by_type: dict[str, int]) -> None:
        if not counts_by_type:
            return
        with self._lock:
            for pii_type, count in counts_by_type.items():
                if count > 0:
                    self._pii_redactions[(provider, pii_type)] += int(count)

    def record_ocr_page(self, backend: str, applied: bool) -> None:
        status = "applied" if applied else "skipped_or_failed"
        with self._lock:
            self._ocr_pages[(backend, status)] += 1

    def record_grounding_failure(self, count: int = 1) -> None:
        if count <= 0:
            return
        with self._lock:
            self._grounding_failures += count

    def record_webhook_delivery(self, channel_type: str, delivered: bool) -> None:
        outcome = "delivered" if delivered else "failed"
        with self._lock:
            self._webhook_deliveries[(channel_type, outcome)] += 1

    def record_retention_purge(self, count: int = 1) -> None:
        if count <= 0:
            return
        with self._lock:
            self._retention_purged += count

    def summary(self) -> dict[str, Any]:
        with self._lock:
            total_http = sum(self._http_requests.values())
            total_llm = sum(self._llm_calls.values())
            total_cost = round(sum(self._llm_cost_usd.values()), 6)
            total_redactions = sum(self._pii_redactions.values())
            total_ocr = sum(self._ocr_pages.values())
            total_prompt_tokens = sum(
                val
                for (_p, _m, direction), val in self._llm_tokens.items()
                if direction == "prompt"
            )
            total_completion_tokens = sum(
                val
                for (_p, _m, direction), val in self._llm_tokens.items()
                if direction == "completion"
            )
            by_provider: dict[str, int] = defaultdict(int)
            for (prov, _m, _tk, _st), val in self._llm_calls.items():
                by_provider[prov] += val

            return {
                "http_requests_total": total_http,
                "llm_calls_total": total_llm,
                "llm_calls_by_provider": dict(by_provider),
                "llm_prompt_tokens_total": total_prompt_tokens,
                "llm_completion_tokens_total": total_completion_tokens,
                "llm_estimated_cost_usd_total": total_cost,
                "pii_redactions_total": total_redactions,
                "ocr_pages_processed_total": total_ocr,
                "citation_grounding_failures_total": self._grounding_failures,
                "webhook_deliveries_total": sum(self._webhook_deliveries.values()),
                "retention_purged_documents_total": self._retention_purged,
            }

    def render_prometheus(self) -> str:
        with self._lock:
            lines: list[str] = [
                "# HELP ai_paralegal_http_requests_total Total HTTP requests handled.",
                "# TYPE ai_paralegal_http_requests_total counter",
            ]
            for (method, path, code), val in sorted(self._http_requests.items()):
                lines.append(
                    f'ai_paralegal_http_requests_total{{method="{_esc_label(method)}",'
                    f'path="{_esc_label(path)}",status="{_esc_label(code)}"}} {val}'
                )

            lines.extend(
                [
                    "# HELP ai_paralegal_http_request_duration_seconds_sum "
                    "Cumulative HTTP latency seconds.",
                    "# TYPE ai_paralegal_http_request_duration_seconds_sum counter",
                ]
            )
            for (method, path), dur_val in sorted(self._http_duration_sum.items()):
                lines.append(
                    f'ai_paralegal_http_request_duration_seconds_sum{{method="{_esc_label(method)}",'
                    f'path="{_esc_label(path)}"}} {dur_val:.6f}'
                )

            lines.extend(
                [
                    "# HELP ai_paralegal_http_request_duration_seconds_count "
                    "Count of timed HTTP requests.",
                    "# TYPE ai_paralegal_http_request_duration_seconds_count counter",
                ]
            )
            for (method, path), val in sorted(self._http_duration_count.items()):
                lines.append(
                    f'ai_paralegal_http_request_duration_seconds_count{{method="{_esc_label(method)}",'
                    f'path="{_esc_label(path)}"}} {val}'
                )

            lines.extend(
                [
                    "# HELP ai_paralegal_llm_calls_total Total structured LLM invocations.",
                    "# TYPE ai_paralegal_llm_calls_total counter",
                ]
            )
            for (prov, mod, tk, st), val in sorted(self._llm_calls.items()):
                lines.append(
                    f'ai_paralegal_llm_calls_total{{provider="{_esc_label(prov)}",'
                    f'model="{_esc_label(mod)}",task_kind="{_esc_label(tk)}",'
                    f'status="{_esc_label(st)}"}} {val}'
                )

            lines.extend(
                [
                    "# HELP ai_paralegal_llm_tokens_total Total LLM input/output tokens.",
                    "# TYPE ai_paralegal_llm_tokens_total counter",
                ]
            )
            for (prov, mod, direction), val in sorted(self._llm_tokens.items()):
                lines.append(
                    f'ai_paralegal_llm_tokens_total{{provider="{_esc_label(prov)}",'
                    f'model="{_esc_label(mod)}",direction="{_esc_label(direction)}"}} {val}'
                )

            lines.extend(
                [
                    "# HELP ai_paralegal_llm_estimated_cost_usd_total "
                    "Estimated cumulative LLM USD cost.",
                    "# TYPE ai_paralegal_llm_estimated_cost_usd_total counter",
                ]
            )
            for (prov, mod), cost_val in sorted(self._llm_cost_usd.items()):
                lines.append(
                    f'ai_paralegal_llm_estimated_cost_usd_total{{provider="{_esc_label(prov)}",'
                    f'model="{_esc_label(mod)}"}} {cost_val:.6f}'
                )

            lines.extend(
                [
                    "# HELP ai_paralegal_pii_redactions_total "
                    "Total PII spans redacted prior to cloud LLM calls.",
                    "# TYPE ai_paralegal_pii_redactions_total counter",
                ]
            )
            for (prov, pii_type), val in sorted(self._pii_redactions.items()):
                lines.append(
                    f'ai_paralegal_pii_redactions_total{{provider="{_esc_label(prov)}",'
                    f'entity_type="{_esc_label(pii_type)}"}} {val}'
                )

            lines.extend(
                [
                    "# HELP ai_paralegal_ocr_pages_total "
                    "Total PDF pages inspected by OCR pipeline.",
                    "# TYPE ai_paralegal_ocr_pages_total counter",
                ]
            )
            for (backend, status), val in sorted(self._ocr_pages.items()):
                lines.append(
                    f'ai_paralegal_ocr_pages_total{{backend="{_esc_label(backend)}",'
                    f'status="{_esc_label(status)}"}} {val}'
                )

            lines.extend(
                [
                    "# HELP ai_paralegal_citation_grounding_failures_total "
                    "Unverified LLM citations caught.",
                    "# TYPE ai_paralegal_citation_grounding_failures_total counter",
                    f"ai_paralegal_citation_grounding_failures_total {self._grounding_failures}",
                ]
            )

            lines.extend(
                [
                    "# HELP ai_paralegal_webhook_deliveries_total "
                    "Total outbound webhook delivery attempts.",
                    "# TYPE ai_paralegal_webhook_deliveries_total counter",
                ]
            )
            for (channel, status), val in sorted(self._webhook_deliveries.items()):
                lines.append(
                    f'ai_paralegal_webhook_deliveries_total{{channel_type="{_esc_label(channel)}",'
                    f'status="{_esc_label(status)}"}} {val}'
                )

            lines.extend(
                [
                    "# HELP ai_paralegal_retention_purged_documents_total "
                    "Total expired documents purged by retention compliance sweeps.",
                    "# TYPE ai_paralegal_retention_purged_documents_total counter",
                    f"ai_paralegal_retention_purged_documents_total {self._retention_purged}",
                ]
            )
            return "\n".join(lines) + "\n"


metrics_registry = MetricsRegistry()
