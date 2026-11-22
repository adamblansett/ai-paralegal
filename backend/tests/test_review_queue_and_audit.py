from httpx import AsyncClient

from tests.conftest import SAMPLES_DIR


async def test_review_queue_transitions_edits_batch_and_audit_trail(
    async_client: AsyncClient,
) -> None:
    matter = (
        await async_client.post(
            "/api/v1/matters",
            json={
                "title": "Audit & Review State Matter",
                "client_name": "Meridian Legal Group LLP",
                "practice_area": "Corporate",
            },
        )
    ).json()
    matter_id = matter["id"]

    msa_bytes = (SAMPLES_DIR / "sample_msa.txt").read_bytes()
    await async_client.post(
        f"/api/v1/matters/{matter_id}/documents",
        files={"file": ("sample_msa.txt", msa_bytes, "text/plain")},
        data={"document_type": "contract"},
    )
    await async_client.post(
        f"/api/v1/matters/{matter_id}/analyses/contract-review",
        json={},
    )

    findings = (await async_client.get(f"/api/v1/matters/{matter_id}/findings")).json()
    assert len(findings) >= 4
    assert all(f["review_status"] == "pending_review" for f in findings)

    first_id = findings[0]["id"]
    second_id = findings[1]["id"]
    batch_ids = [f["id"] for f in findings[2:4]]

    # Approve with attorney edit & notes
    approve_resp = await async_client.patch(
        f"/api/v1/findings/{first_id}/review",
        json={
            "review_status": "approved",
            "reviewer_name": "E. Rostova, Esq.",
            "reviewer_notes": "Confirmed against signed Section 6.",
            "edited_summary": "Attorney-verified summary: Client indemnity is uncapped.",
        },
    )
    assert approve_resp.status_code == 200
    approved = approve_resp.json()
    assert approved["review_status"] == "approved"
    assert approved["reviewer_name"] == "E. Rostova, Esq."
    assert approved["edited_summary"] == "Attorney-verified summary: Client indemnity is uncapped."

    # Flag second finding
    flag_resp = await async_client.patch(
        f"/api/v1/findings/{second_id}/review",
        json={
            "review_status": "flagged",
            "reviewer_name": "E. Rostova, Esq.",
            "reviewer_notes": "Escalate to managing partner before client call.",
        },
    )
    assert flag_resp.status_code == 200
    assert flag_resp.json()["review_status"] == "flagged"

    # Batch approve remaining slice
    batch_resp = await async_client.post(
        "/api/v1/findings/batch-review",
        json={
            "finding_ids": batch_ids,
            "review_status": "approved",
            "reviewer_name": "M. Partner",
            "reviewer_notes": "Batch sign-off",
        },
    )
    assert batch_resp.status_code == 200
    assert batch_resp.json()["updated_count"] == len(batch_ids)

    # Verify Matter stats reflect updated review statuses
    matter_detail = (await async_client.get(f"/api/v1/matters/{matter_id}")).json()
    assert matter_detail["stats"]["approved_count"] == 1 + len(batch_ids)
    assert matter_detail["stats"]["flagged_count"] == 1

    # Verify Audit Trail captures LLM prompt/spans AND human review state transitions
    audit_logs = (await async_client.get(f"/api/v1/matters/{matter_id}/audit-logs")).json()
    actions = [log["action"] for log in audit_logs]
    assert "llm_invocation" in actions
    assert "finding_reviewed" in actions
    assert "document_indexed" in actions

    llm_log = next(log for log in audit_logs if log["action"] == "llm_invocation")
    assert llm_log["provider"] == "mock"
    assert llm_log["prompt_template"] == "contract_review_v1"
    assert llm_log["prompt_version"] == "1.0.0"
    assert len(llm_log["retrieved_spans_json"]) >= 1

    review_logs = [log for log in audit_logs if log["action"] == "finding_reviewed"]
    assert any(
        log["previous_state"] == "pending_review" and log["new_state"] == "approved"
        for log in review_logs
    )
    assert any(
        log["previous_state"] == "pending_review" and log["new_state"] == "flagged"
        for log in review_logs
    )
