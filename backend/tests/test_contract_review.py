from httpx import AsyncClient

from tests.conftest import SAMPLES_DIR


async def test_contract_review_extracts_risks_missing_protections_and_verified_citations(
    async_client: AsyncClient,
) -> None:
    matter = (
        await async_client.post(
            "/api/v1/matters",
            json={
                "title": "Apex Cloud MSA Review",
                "client_name": "Meridian Legal Group LLP",
                "practice_area": "Commercial Contracts",
            },
        )
    ).json()
    matter_id = matter["id"]

    msa_bytes = (SAMPLES_DIR / "sample_msa.txt").read_bytes()
    upload = (
        await async_client.post(
            f"/api/v1/matters/{matter_id}/documents",
            files={"file": ("sample_msa.txt", msa_bytes, "text/plain")},
            data={"document_type": "contract"},
        )
    ).json()
    doc_id = upload["document"]["id"]

    review_resp = await async_client.post(
        f"/api/v1/matters/{matter_id}/analyses/contract-review",
        json={"document_ids": [doc_id]},
    )
    assert review_resp.status_code == 202
    task = review_resp.json()
    assert task["status"] == "completed"
    assert task["resolved_provider"] == "mock"
    assert "Does not constitute legal advice" in task["result_json"]["disclaimer"]
    assert task["result_json"]["overall_risk_level"] in {"high", "critical"}

    findings = (await async_client.get(f"/api/v1/matters/{matter_id}/findings")).json()
    assert len(findings) >= 8

    categories = {f["category"] for f in findings}
    assert {
        "indemnification",
        "limitation_of_liability",
        "ip_assignment",
        "confidentiality",
        "termination",
        "governing_law",
        "renewal_notice",
    }.issubset(categories)

    missing = [f for f in findings if f["finding_type"] == "missing_protection"]
    assert len(missing) >= 1

    risk_flags = [f for f in findings if f["finding_type"] == "risk_flag"]
    assert len(risk_flags) >= 4
    assert any(f["requires_escalation"] for f in risk_flags)

    # Every risk flag must carry verified citations grounded in sample_msa.txt
    for rf in risk_flags:
        assert len(rf["citations_json"]) >= 1
        assert rf["grounding_score"] >= 0.75
        assert any(
            c["verification_status"] in {"verified_exact", "verified_fuzzy"}
            for c in rf["citations_json"]
        )
        assert rf["citations_json"][0]["document_filename"] == "sample_msa.txt"
