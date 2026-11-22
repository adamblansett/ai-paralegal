from httpx import AsyncClient

from app.services.events import event_bus
from tests.conftest import SAMPLES_DIR, register_and_login


async def test_source_content_endpoint_batch_matrix_and_sse_events(
    raw_client: AsyncClient,
    async_client: AsyncClient,
) -> None:
    matter_resp = await async_client.post(
        "/api/v1/matters",
        json={
            "title": "Portfolio Review & Source Viewer Test",
            "client_name": "Summit Bio",
            "practice_area": "Commercial Contracts",
        },
    )
    assert matter_resp.status_code == 201
    matter_id = matter_resp.json()["id"]

    # Upload 2 contracts into matter
    msa_bytes = (SAMPLES_DIR / "sample_msa.txt").read_bytes()
    doc1_resp = await async_client.post(
        f"/api/v1/matters/{matter_id}/documents",
        files={"file": ("acme_msa.txt", msa_bytes, "text/plain")},
        data={"document_type": "contract"},
    )
    assert doc1_resp.status_code == 202
    doc1 = doc1_resp.json()["document"]

    doc2_resp = await async_client.post(
        f"/api/v1/matters/{matter_id}/documents",
        files={
            "file": (
                "vendor_sow.txt",
                (
                    b"MASTER STATEMENT OF WORK\n"
                    b"1. Indemnification. Customer shall defend and indemnify Vendor uncapped.\n"
                    b"2. Limitation of Liability. Vendor liability capped at 1 month fees.\n"
                ),
                "text/plain",
            )
        },
        data={"document_type": "contract"},
    )
    assert doc2_resp.status_code == 202
    doc2 = doc2_resp.json()["document"]

    # 1. Source content endpoint inline + attachment + RBAC check
    inline_resp = await async_client.get(f"/api/v1/documents/{doc1['id']}/content")
    assert inline_resp.status_code == 200
    assert "inline" in inline_resp.headers["content-disposition"]
    assert inline_resp.headers["x-content-type-options"] == "nosniff"
    assert b"INDEMNIFICATION" in inline_resp.content

    dl_resp = await async_client.get(f"/api/v1/documents/{doc1['id']}/content?download=true")
    assert dl_resp.status_code == 200
    assert "attachment" in dl_resp.headers["content-disposition"]

    outsider_token, _ = await register_and_login(
        raw_client, email="outsider.src@firm.example.com", full_name="Outsider Counsel"
    )
    forbidden = await raw_client.get(
        f"/api/v1/documents/{doc1['id']}/content",
        headers={"Authorization": f"Bearer {outsider_token}"},
    )
    assert forbidden.status_code == 403

    # 2. Batch contract review & Portfolio Risk Matrix
    batch_resp = await async_client.post(
        f"/api/v1/matters/{matter_id}/analyses/batch-contract-review",
        json={"provider": "mock"},
    )
    assert batch_resp.status_code == 202
    batch_task = batch_resp.json()
    assert batch_task["status"] == "completed"
    assert batch_task["result_json"]["processed_documents"] == 2
    assert batch_task["result_json"]["total_findings"] >= 4

    matrix_resp = await async_client.get(f"/api/v1/matters/{matter_id}/analyses/risk-matrix")
    assert matrix_resp.status_code == 200
    matrix = matrix_resp.json()
    assert matrix["total_documents"] == 2
    assert len(matrix["rows"]) == 2
    filenames = {row["filename"] for row in matrix["rows"]}
    assert filenames == {"acme_msa.txt", "vendor_sow.txt"}
    assert doc2["id"] in {row["document_id"] for row in matrix["rows"]}

    matrix_csv = await async_client.get(
        f"/api/v1/matters/{matter_id}/exports/risk-matrix?format=csv"
    )
    assert matrix_csv.status_code == 200
    assert "acme_msa.txt" in matrix_csv.text
    assert "vendor_sow.txt" in matrix_csv.text

    # 3. SSE endpoint & event_bus unit publish
    sse_resp = await async_client.get(f"/api/v1/matters/{matter_id}/events?once=true")
    assert sse_resp.status_code == 200
    assert sse_resp.headers["content-type"].startswith("text/event-stream")
    assert "event: snapshot" in sse_resp.text
    assert "acme_msa.txt" in sse_resp.text

    async with event_bus.subscribe(matter_id) as queue:
        event_bus.publish(matter_id, "task_progress", {"status": "running"})
        evt = queue.get_nowait()
        assert evt["event"] == "task_progress"
        assert evt["data"]["status"] == "running"
