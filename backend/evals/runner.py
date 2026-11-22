import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.core.metrics import estimate_llm_cost_usd
from app.llm.citations import compute_grounding_score, evaluate_escalation, verify_citations
from app.llm.prompts import (
    PROMPT_VERSION,
    STANDARD_CLAUSE_TYPES,
    build_contract_review_prompt,
    build_discovery_synthesis_prompt,
    build_research_qa_prompt,
)
from app.llm.providers.base import ProviderError
from app.llm.router import DEFAULT_TASK_MODELS, ModelRouter
from app.llm.types import (
    ContractReviewLLMOutput,
    DiscoverySynthesisLLMOutput,
    LLMRequest,
    RawCitation,
    ResearchAnswerLLMOutput,
    SourceChunkContext,
)
from app.services.chunker import chunk_segments
from app.services.parsers import parse_document_bytes
from app.services.playbooks import DEFAULT_SAAS_MSA_RULES

EVAL_DIR = Path(__file__).resolve().parent
DATASET_PATH = EVAL_DIR / "dataset.json"
SAMPLES_DIR = EVAL_DIR.parents[1] / "samples"


class CaseMetric(BaseModel):
    case_id: str
    task_kind: str
    passed: bool
    latency_ms: int
    grounding_score: float
    details: dict[str, Any]


class EvaluationReport(BaseModel):
    provider: str
    model: str | None
    dataset_version: str
    clause_recall: float
    risk_recall: float
    missing_protection_recall: float
    contradiction_recall: float
    omission_recall: float
    qa_keyword_recall: float
    citation_grounding_rate: float
    hallucination_catch_rate: float
    json_validity_rate: float
    avg_latency_ms: float
    cases: list[CaseMetric]


class ModelComparisonRow(BaseModel):
    target: str
    provider: str
    model: str
    status: Literal["completed", "skipped", "error"]
    skip_reason: str | None = None
    report: EvaluationReport | None = None
    composite_score: float = 0.0
    estimated_cost_usd: float = 0.0


class ComparisonBenchmarkReport(BaseModel):
    dataset_version: str
    results: list[ModelComparisonRow]
    recommended_routing: dict[str, str] = Field(default_factory=dict)


def _load_sample_chunks(filenames: list[str]) -> list[SourceChunkContext]:
    contexts: list[SourceChunkContext] = []
    ref_num = 1
    for name in filenames:
        path = SAMPLES_DIR / name
        raw_bytes = path.read_bytes()
        parsed = parse_document_bytes(name, raw_bytes)
        drafts = chunk_segments(parsed.segments)
        doc_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, name))
        for d in drafts:
            cid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{name}:{d.chunk_index}"))
            contexts.append(
                SourceChunkContext(
                    chunk_ref=f"C{ref_num}",
                    chunk_id=cid,
                    document_id=doc_id,
                    document_filename=name,
                    document_type=parsed.detected_document_type,
                    chunk_index=d.chunk_index,
                    page_number=d.page_number,
                    paragraph_index=d.paragraph_index,
                    section_heading=d.section_heading,
                    char_start=d.char_start,
                    char_end=d.char_end,
                    content=d.content,
                )
            )
            ref_num += 1
    return contexts


async def run_benchmark(
    *,
    provider: str = "mock",
    model: str | None = None,
    settings: Settings | None = None,
) -> EvaluationReport:
    """Run the legal grounding, recall, and hallucination benchmark suite."""
    cfg = settings or get_settings()
    router = ModelRouter(cfg)
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))

    case_metrics: list[CaseMetric] = []
    latencies: list[int] = []
    all_citation_scores: list[float] = []
    json_successes = 0
    json_attempts = 0

    clause_recalls: list[float] = []
    risk_recalls: list[float] = []
    missing_recalls: list[float] = []
    contradiction_recalls: list[float] = []
    omission_recalls: list[float] = []
    qa_recalls: list[float] = []

    # 1. Contract Review Cases
    rule_map = {str(r["clause_type"]): r for r in DEFAULT_SAAS_MSA_RULES}
    for case in dataset["contract_review_cases"]:
        chunks = _load_sample_chunks([case["sample_file"]])
        chunk_map = {c.chunk_id: c for c in chunks}
        tmpl, sys_p, usr_p = build_contract_review_prompt(
            chunks, clause_types=STANDARD_CLAUSE_TYPES
        )
        req = LLMRequest(
            task_kind="contract_review",
            prompt_template=tmpl,
            prompt_version=PROMPT_VERSION,
            system_prompt=sys_p,
            user_prompt=usr_p,
            context_chunks=chunks,
            metadata={"clause_types": STANDARD_CLAUSE_TYPES, "playbook_rules": rule_map},
        )
        json_attempts += 1
        resp = await router.generate_structured(
            req, ContractReviewLLMOutput, provider=provider, model=model, enable_fallback=False
        )
        json_successes += 1
        latencies.append(resp.latency_ms)

        found_cats = {f.category for f in resp.data.findings}
        risk_cats = {
            f.category for f in resp.data.findings if f.finding_type == "risk_flag"
        }
        missing_cats = {
            f.category for f in resp.data.findings if f.finding_type == "missing_protection"
        }

        c_rec = len(set(case["expected_clauses"]) & found_cats) / len(case["expected_clauses"])
        r_rec = len(set(case["expected_risk_categories"]) & risk_cats) / len(
            case["expected_risk_categories"]
        )
        m_rec = len(set(case["expected_missing_categories"]) & missing_cats) / len(
            case["expected_missing_categories"]
        )
        clause_recalls.append(c_rec)
        risk_recalls.append(r_rec)
        missing_recalls.append(m_rec)

        g_scores: list[float] = []
        for f in resp.data.findings:
            if f.citations:
                ver = verify_citations(f.citations, chunk_map)
                g = compute_grounding_score(ver)
                g_scores.append(g)
                all_citation_scores.append(g)
        avg_g = sum(g_scores) / len(g_scores) if g_scores else 0.0

        case_metrics.append(
            CaseMetric(
                case_id=case["id"],
                task_kind="contract_review",
                passed=c_rec >= 0.85 and r_rec >= 0.80 and avg_g >= 0.80,
                latency_ms=resp.latency_ms,
                grounding_score=round(avg_g, 3),
                details={
                    "clause_recall": round(c_rec, 3),
                    "risk_recall": round(r_rec, 3),
                    "missing_protection_recall": round(m_rec, 3),
                },
            )
        )

    # 2. Discovery Synthesis Cases
    for case in dataset["discovery_cases"]:
        chunks = _load_sample_chunks(case["sample_files"])
        chunk_map = {c.chunk_id: c for c in chunks}
        tmpl, sys_p, usr_p = build_discovery_synthesis_prompt(chunks)
        req = LLMRequest(
            task_kind="discovery_synthesis",
            prompt_template=tmpl,
            prompt_version=PROMPT_VERSION,
            system_prompt=sys_p,
            user_prompt=usr_p,
            context_chunks=chunks,
        )
        json_attempts += 1
        resp_d = await router.generate_structured(
            req, DiscoverySynthesisLLMOutput, provider=provider, model=model, enable_fallback=False
        )
        json_successes += 1
        latencies.append(resp_d.latency_ms)

        con_text = " ".join(
            f"{c.title} {c.description}" for c in resp_d.data.contradictions
        ).lower()
        om_text = " ".join(f"{o.title} {o.description}" for o in resp_d.data.omissions).lower()
        con_hits = sum(
            1 for term in case["expected_contradiction_terms"] if term.lower() in con_text
        )
        om_hits = sum(1 for term in case["expected_omission_terms"] if term.lower() in om_text)
        con_rec = con_hits / len(case["expected_contradiction_terms"])
        om_rec = om_hits / len(case["expected_omission_terms"])
        contradiction_recalls.append(con_rec)
        omission_recalls.append(om_rec)

        g_scores = []
        for item_cits in (
            [c.citations for c in resp_d.data.contradictions]
            + [o.citations for o in resp_d.data.omissions]
            + [t.citations for t in resp_d.data.timeline_events]
        ):
            if item_cits:
                g = compute_grounding_score(verify_citations(item_cits, chunk_map))
                g_scores.append(g)
                all_citation_scores.append(g)
        avg_g = sum(g_scores) / len(g_scores) if g_scores else 0.0

        case_metrics.append(
            CaseMetric(
                case_id=case["id"],
                task_kind="discovery_synthesis",
                passed=con_rec >= 0.75 and om_rec >= 1.0 and avg_g >= 0.80,
                latency_ms=resp_d.latency_ms,
                grounding_score=round(avg_g, 3),
                details={
                    "contradiction_recall": round(con_rec, 3),
                    "omission_recall": round(om_rec, 3),
                    "timeline_events": len(resp_d.data.timeline_events),
                },
            )
        )

    # 3. Research Q&A Cases
    for case in dataset["qa_cases"]:
        chunks = _load_sample_chunks(case["sample_files"])
        chunk_map = {c.chunk_id: c for c in chunks}
        tmpl, sys_p, usr_p = build_research_qa_prompt(case["question"], chunks)
        req = LLMRequest(
            task_kind="qa_research",
            prompt_template=tmpl,
            prompt_version=PROMPT_VERSION,
            system_prompt=sys_p,
            user_prompt=usr_p,
            context_chunks=chunks,
            metadata={"question": case["question"]},
        )
        json_attempts += 1
        resp_q = await router.generate_structured(
            req, ResearchAnswerLLMOutput, provider=provider, model=model, enable_fallback=False
        )
        json_successes += 1
        latencies.append(resp_q.latency_ms)

        ans_lower = resp_q.data.answer.lower()
        hits = sum(1 for t in case["expected_answer_terms"] if t.lower() in ans_lower)
        qa_rec = hits / len(case["expected_answer_terms"])
        qa_recalls.append(qa_rec)

        ver = verify_citations(resp_q.data.citations, chunk_map)
        g = compute_grounding_score(ver)
        all_citation_scores.append(g)

        case_metrics.append(
            CaseMetric(
                case_id=case["id"],
                task_kind="qa_research",
                passed=qa_rec >= 0.50 and g >= 0.75,
                latency_ms=resp_q.latency_ms,
                grounding_score=round(g, 3),
                details={"qa_keyword_recall": round(qa_rec, 3)},
            )
        )

    # 4. Adversarial Hallucination Traps
    caught = 0
    traps = dataset["hallucination_traps"]
    for trap in traps:
        cmap = {
            trap["chunk_id"]: SourceChunkContext(
                chunk_ref="C1",
                chunk_id=trap["chunk_id"],
                document_id="eval-doc",
                document_filename="sample_msa.txt",
                chunk_index=0,
                content=trap["real_content"],
            )
        }
        ver = verify_citations(
            [RawCitation(chunk_id=trap["fake_chunk_id"], quote=trap["fabricated_quote"])],
            cmap,
        )
        g = compute_grounding_score(ver)
        unverified_cnt = sum(1 for v in ver if v.verification_status == "unverified")
        escalate, _ = evaluate_escalation(
            confidence=0.90,
            grounding_score=g,
            severity="medium",
            unverified_count=unverified_cnt,
            settings=cfg,
        )
        trap_caught = unverified_cnt == 1 and escalate
        if trap_caught:
            caught += 1
        case_metrics.append(
            CaseMetric(
                case_id=trap["id"],
                task_kind="hallucination_trap",
                passed=trap_caught,
                latency_ms=0,
                grounding_score=g,
                details={"unverified_detected": unverified_cnt, "escalated": escalate},
            )
        )

    def _avg(vals: list[float]) -> float:
        return round(sum(vals) / len(vals), 3) if vals else 0.0

    return EvaluationReport(
        provider=provider,
        model=model,
        dataset_version=dataset["version"],
        clause_recall=_avg(clause_recalls),
        risk_recall=_avg(risk_recalls),
        missing_protection_recall=_avg(missing_recalls),
        contradiction_recall=_avg(contradiction_recalls),
        omission_recall=_avg(omission_recalls),
        qa_keyword_recall=_avg(qa_recalls),
        citation_grounding_rate=_avg(all_citation_scores),
        hallucination_catch_rate=round(caught / len(traps), 3) if traps else 1.0,
        json_validity_rate=round(json_successes / max(1, json_attempts), 3),
        avg_latency_ms=round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
        cases=case_metrics,
    )


def format_markdown_report(report: EvaluationReport) -> str:
    lines = [
        f"# AI Paralegal Evaluation Benchmark ({report.provider})",
        "",
        "| Metric | Score |",
        "| --- | --- |",
        f"| Clause Recall | {report.clause_recall:.1%} |",
        f"| Risk Flag Recall | {report.risk_recall:.1%} |",
        f"| Missing Protection Recall | {report.missing_protection_recall:.1%} |",
        f"| Contradiction Recall | {report.contradiction_recall:.1%} |",
        f"| Omission Recall | {report.omission_recall:.1%} |",
        f"| Q&A Keyword Recall | {report.qa_keyword_recall:.1%} |",
        f"| Citation Grounding Rate | {report.citation_grounding_rate:.1%} |",
        f"| Hallucination Catch Rate | {report.hallucination_catch_rate:.1%} |",
        f"| JSON Validity Rate | {report.json_validity_rate:.1%} |",
        f"| Avg Latency (ms) | {report.avg_latency_ms:.1f} ms |",
    ]
    return "\n".join(lines)


def _compute_composite_score(report: EvaluationReport) -> float:
    score = (
        0.25 * report.clause_recall
        + 0.15 * report.risk_recall
        + 0.15 * report.contradiction_recall
        + 0.25 * report.citation_grounding_rate
        + 0.10 * report.hallucination_catch_rate
        + 0.10 * report.json_validity_rate
    )
    return round(score, 3)


async def probe_live_targets(settings: Settings | None = None) -> list[tuple[str, str]]:
    """Discover reachable Ollama models & configured Gemini models plus offline mock baseline."""
    cfg = settings or get_settings()
    targets: list[tuple[str, str]] = [("mock", "mock-legal-deterministic-v1")]

    # Probe local Ollama server with short timeout
    tags_url = f"{cfg.ollama_base_url.rstrip('/')}/api/tags"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(tags_url)
            if resp.status_code == 200:
                models = [
                    str(m.get("name"))
                    for m in resp.json().get("models", [])
                    if isinstance(m, dict) and m.get("name")
                ]
                for mod in models[:4]:
                    targets.append(("ollama", mod))
    except Exception:
        pass

    if not any(prov == "ollama" for prov, _ in targets):
        targets.append(("ollama", cfg.ollama_default_model))

    if cfg.gemini_api_key:
        targets.append(("gemini", cfg.gemini_default_model))
    else:
        targets.append(("gemini", cfg.gemini_default_model))

    return targets


async def run_comparison_benchmark(
    targets: list[tuple[str, str]],
    *,
    settings: Settings | None = None,
) -> ComparisonBenchmarkReport:
    """Run benchmarks across multiple `(provider, model)` pairs, skipping unconfigured ones."""
    cfg = settings or get_settings()
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    rows: list[ModelComparisonRow] = []

    for prov, mod in targets:
        target_label = f"{prov}:{mod}"
        if prov == "gemini" and not cfg.gemini_api_key:
            rows.append(
                ModelComparisonRow(
                    target=target_label,
                    provider=prov,
                    model=mod,
                    status="skipped",
                    skip_reason="GEMINI_API_KEY not configured",
                )
            )
            continue

        try:
            report = await run_benchmark(provider=prov, model=mod, settings=cfg)
            est_cost = round(
                estimate_llm_cost_usd(prov, mod, 3500 * len(report.cases), 900 * len(report.cases)),
                6,
            )
            rows.append(
                ModelComparisonRow(
                    target=target_label,
                    provider=prov,
                    model=mod,
                    status="completed",
                    report=report,
                    composite_score=_compute_composite_score(report),
                    estimated_cost_usd=est_cost,
                )
            )
        except ProviderError as exc:
            rows.append(
                ModelComparisonRow(
                    target=target_label,
                    provider=prov,
                    model=mod,
                    status="skipped",
                    skip_reason=str(exc),
                )
            )
        except Exception as exc:
            rows.append(
                ModelComparisonRow(
                    target=target_label,
                    provider=prov,
                    model=mod,
                    status="error",
                    skip_reason=str(exc),
                )
            )

    completed_non_mock = [r for r in rows if r.status == "completed" and r.provider != "mock"]
    best_live = (
        max(completed_non_mock, key=lambda r: r.composite_score)
        if completed_non_mock
        else next((r for r in rows if r.status == "completed"), None)
    )
    recommended: dict[str, str] = {}
    for task_kind, defaults in DEFAULT_TASK_MODELS.items():
        if best_live is not None and best_live.provider != "mock":
            recommended[task_kind] = best_live.target
        else:
            default_ollama = defaults.get("ollama", cfg.ollama_default_model)
            recommended[task_kind] = f"ollama:{default_ollama} (fallback: mock)"

    return ComparisonBenchmarkReport(
        dataset_version=str(dataset.get("version", "1.0")),
        results=rows,
        recommended_routing=recommended,
    )


def format_comparison_markdown(comp: ComparisonBenchmarkReport) -> str:
    """Format multi-model comparison scorecard as Markdown."""
    lines = [
        f"# AI Paralegal Multi-Model Evaluation Scorecard (Dataset v{comp.dataset_version})",
        "",
        "| Target | Status | Composite | Clause Recall | Grounding | Hallucination Catch | "
        "Avg Latency | Notes |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in comp.results:
        if r.status == "completed" and r.report is not None:
            rep = r.report
            lines.append(
                f"| `{r.target}` | completed | {r.composite_score:.1%} | "
                f"{rep.clause_recall:.1%} | {rep.citation_grounding_rate:.1%} | "
                f"{rep.hallucination_catch_rate:.1%} | {rep.avg_latency_ms:.1f} ms | "
                f"Est. ${r.estimated_cost_usd:.4f} |"
            )
        else:
            note = (r.skip_reason or "unavailable").replace("|", "\\|")[:80]
            lines.append(f"| `{r.target}` | {r.status} | — | — | — | — | — | {note} |")

    lines.extend(["", "## Recommended Per-Task Routing Defaults", ""])
    for task_kind, chosen in comp.recommended_routing.items():
        lines.append(f"- `{task_kind}` -> `{chosen}`")
    return "\n".join(lines)


def _parse_compare_targets(raw: str) -> list[tuple[str, str]]:
    parsed: list[tuple[str, str]] = []
    for item in raw.split(","):
        cleaned = item.strip()
        if not cleaned:
            continue
        if ":" in cleaned:
            prov, mod = cleaned.split(":", 1)
            parsed.append((prov.strip().lower(), mod.strip()))
        else:
            parsed.append((cleaned.lower(), "default"))
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run AI Paralegal LLM evaluation benchmarks.")
    parser.add_argument("--provider", default="mock", choices=["mock", "ollama", "gemini"])
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--compare",
        default=None,
        help="Comma-separated provider:model targets (e.g., mock:default,ollama:qwen-2.5-7b)",
    )
    parser.add_argument(
        "--probe-live",
        action="store_true",
        help="Auto-probe live Ollama and Gemini models alongside offline mock baseline.",
    )
    parser.add_argument("--output", default=None, help="Optional JSON report output path.")
    parser.add_argument("--markdown", action="store_true", help="Print Markdown summary table.")
    args = parser.parse_args(argv)

    if args.probe_live or args.compare:
        targets = (
            _parse_compare_targets(args.compare)
            if args.compare
            else asyncio.run(probe_live_targets())
        )
        comp = asyncio.run(run_comparison_benchmark(targets))
        if args.output:
            Path(args.output).write_text(comp.model_dump_json(indent=2), encoding="utf-8")
        if args.markdown:
            print(format_comparison_markdown(comp))
        else:
            print(comp.model_dump_json(indent=2))
        completed = [r for r in comp.results if r.status == "completed"]
        if not completed:
            return 1
        return 0 if all(all(c.passed for c in r.report.cases) for r in completed if r.report) else 1

    report = asyncio.run(run_benchmark(provider=args.provider, model=args.model))
    if args.output:
        Path(args.output).write_text(report.model_dump_json(indent=2), encoding="utf-8")

    if args.markdown:
        print(format_markdown_report(report))
    else:
        print(report.model_dump_json(indent=2))

    return 0 if all(c.passed for c in report.cases) else 1


if __name__ == "__main__":
    sys.exit(main())
