from evals.runner import format_markdown_report, run_benchmark


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
