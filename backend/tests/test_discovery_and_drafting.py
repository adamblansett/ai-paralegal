from httpx import AsyncClient

from tests.conftest import SAMPLES_DIR


async def _setup_matter_with_msa_and_emails(async_client: AsyncClient) -> str:
    matter = (
        await async_client.post(
            "/api/v1/matters",
            json={
                "title": "Meridian v. Apex Cloud Discovery Matter",
                "client_name": "Meridian Legal Group LLP",
                "practice_area": "Commercial Litigation",
                "jurisdiction": "Delaware",
            },
        )
    ).json()
    matter_id = str(matter["id"])

    msa_bytes = (SAMPLES_DIR / "sample_msa.txt").read_bytes()
    eml_bytes = (SAMPLES_DIR / "sample_discovery_emails.eml").read_bytes()

    await async_client.post(
        f"/api/v1/matters/{matter_id}/documents",
        files={"file": ("sample_msa.txt", msa_bytes, "text/plain")},
        data={"document_type": "contract"},
    )
    await async_client.post(
        f"/api/v1/matters/{matter_id}/documents",
        files={"file": ("sample_discovery_emails.eml", eml_bytes, "message/rfc822")},
        data={"document_type": "email"},
    )
    return matter_id


async def test_hybrid_search_discovery_synthesis_exports_qa_and_drafting(
    async_client: AsyncClient,
) -> None:
    matter_id = await _setup_matter_with_msa_and_emails(async_client)

    # 1. Hybrid search
    search_resp = await async_client.post(
        f"/api/v1/matters/{matter_id}/search",
        json={"query": "annual subscription fee $185,000 $240,000", "top_k": 5},
    )
    assert search_resp.status_code == 200
    hits = search_resp.json()["hits"]
    assert len(hits) >= 2
    assert hits[0]["rrf_score"] > 0

    # 2. Discovery synthesis (timeline, entities, contradictions, omissions)
    syn_resp = await async_client.post(
        f"/api/v1/matters/{matter_id}/analyses/discovery-synthesis",
        json={"focus_topics": ["fees", "liability cap", "Exhibit B"]},
    )
    assert syn_resp.status_code == 202
    syn_task = syn_resp.json()
    assert syn_task["status"] == "completed"
    assert syn_task["result_json"]["contradiction_count"] >= 2
    assert syn_task["result_json"]["omission_count"] >= 1
    assert len(syn_task["result_json"]["timeline_events"]) >= 3

    contradictions = (
        await async_client.get(f"/api/v1/matters/{matter_id}/findings?finding_type=contradiction")
    ).json()
    assert len(contradictions) >= 2
    assert any("$185,000" in c["title"] for c in contradictions)

    omissions = (
        await async_client.get(f"/api/v1/matters/{matter_id}/findings?finding_type=omission")
    ).json()
    assert len(omissions) >= 1

    # 3. Structured Exports (CSV, JSON, Markdown)
    csv_table = await async_client.get(
        f"/api/v1/matters/{matter_id}/exports/review-table?format=csv"
    )
    assert csv_table.status_code == 200
    assert "text/csv" in csv_table.headers["content-type"]
    assert "Finding Type" in csv_table.text

    md_chron = await async_client.get(
        f"/api/v1/matters/{matter_id}/exports/chronology?format=markdown"
    )
    assert md_chron.status_code == 200
    assert "Does not constitute legal advice" in md_chron.text
    assert "| Date |" in md_chron.text

    json_table = await async_client.get(
        f"/api/v1/matters/{matter_id}/exports/review-table?format=json"
    )
    assert json_table.status_code == 200
    assert len(json_table.json()["findings"]) >= 5

    # 4. Citation-Grounded Research Q&A
    qa_resp = await async_client.post(
        f"/api/v1/matters/{matter_id}/research/query",
        json={
            "question": "What does Exhibit B SLA schedule say about security breach notification?",
            "save_to_queue": True,
        },
    )
    assert qa_resp.status_code == 200
    qa = qa_resp.json()
    assert qa["grounding_score"] >= 0.75
    assert len(qa["citations"]) >= 1
    assert len(qa["missing_context"]) >= 1
    assert "Does not constitute legal advice" in qa["disclaimer"]
    assert qa["finding_id"] is not None

    # 5. Citation-Grounded Drafting -> Review Queue
    draft_resp = await async_client.post(
        f"/api/v1/matters/{matter_id}/drafts",
        json={
            "draft_type": "internal_memo",
            "instructions": (
                "Prepare an internal memo detailing the fee discrepancy and missing Exhibit B SLA."
            ),
        },
    )
    assert draft_resp.status_code == 202
    draft_task = draft_resp.json()
    assert draft_task["status"] == "completed"
    assert "ATTORNEY VERIFICATION REQUIRED" in draft_task["result_json"]["draft_markdown"]
    assert draft_task["result_json"]["grounding_score"] >= 0.75

    draft_findings = (
        await async_client.get(f"/api/v1/matters/{matter_id}/findings?finding_type=draft_output")
    ).json()
    assert len(draft_findings) == 1
    assert draft_findings[0]["review_status"] == "pending_review"
    assert draft_findings[0]["requires_escalation"] is True
