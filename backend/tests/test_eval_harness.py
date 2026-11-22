from app.core.config import Settings
from app.llm.router import ModelRouter
from evals.runner import (
    format_comparison_markdown,
    format_markdown_report,
    run_benchmark,
    run_comparison_benchmark,
)


async def test_eval_benchmark_harness_passes_with_mock_provider() -> None:
    report = await run_benchmark(provider="mock")
    assert report.clause_recall >= 0.85
    assert report.risk_recall >= 0.80
    assert report.missing_protection_recall >= 1.0
    assert report.contradiction_recall >= 1.0
    assert report.omission_recall >= 1.0
    assert report.citation_grounding_rate >= 0.90
    assert report.hallucination_catch_rate == 1.0
    assert report.json_validity_rate == 1.0
    assert all(case.passed for case in report.cases)

    md = format_markdown_report(report)
    assert "Hallucination Catch Rate" in md
    assert "100.0%" in md


async def test_multi_model_comparison_and_task_routing() -> None:
    cfg = Settings(gemini_api_key="")
    router = ModelRouter(cfg)
    assert router.resolve_task_model("ollama", "contract_review", None) == "qwen-2.5-7b-instruct"
    assert router.resolve_task_model("gemini", "discovery_synthesis", None) == "gemini-2.5-pro"

    comp = await run_comparison_benchmark(
        [
            ("mock", "mock-legal-deterministic-v1"),
            ("gemini", "gemini-2.5-flash"),
        ],
        settings=cfg,
    )
    assert len(comp.results) == 2
    assert comp.results[0].status == "completed"
    assert comp.results[0].composite_score == 1.0
    assert comp.results[1].status == "skipped"
    scorecard_md = format_comparison_markdown(comp)
    assert "mock:mock-legal-deterministic-v1" in scorecard_md
    assert "GEMINI_API_KEY not configured" in scorecard_md
