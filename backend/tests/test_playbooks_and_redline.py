import io
import zipfile

from httpx import AsyncClient

from tests.conftest import SAMPLES_DIR


async def test_playbook_crud_playbook_review_and_word_redline_export(
    async_client: AsyncClient,
) -> None:
    # 1. Verify seeded default playbook exists
    list_pb = await async_client.get("/api/v1/playbooks")
    assert list_pb.status_code == 200
    playbooks = list_pb.json()
    assert any(pb["is_default"] for pb in playbooks)

    # 2. Create custom buyer playbook
    create_pb = await async_client.post(
        "/api/v1/playbooks",
        json={
            "name": "Strict Healthcare SaaS Playbook",
            "practice_area": "Healthcare Tech",
            "description": "Strict caps and HIPAA breach timelines.",
            "rules": [
                {
                    "clause_type": "limitation_of_liability",
                    "standard_position": "24 months fees cap",
                    "fallback_position": (
                        "CUSTOM FALLBACK: Liability shall be capped at 24 months of fees "
                        "with uncapped carve-outs for HIPAA breaches."
                    ),
                    "red_flag_patterns": ["one (1) month", "$20,000"],
                    "required": True,
                    "severity_if_deviated": "critical",
                }
            ],
        },
    )
    assert create_pb.status_code == 201
    custom_pb = create_pb.json()

    # 3. Create matter, upload MSA, and run contract review with custom playbook
    matter = (
        await async_client.post(
            "/api/v1/matters",
            json={
                "title": "Playbook & DOCX Redline Matter",
                "client_name": "Meridian Health",
                "practice_area": "Healthcare Tech",
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

    rev = await async_client.post(
        f"/api/v1/matters/{matter_id}/analyses/contract-review",
        json={"playbook_id": custom_pb["id"]},
    )
    assert rev.status_code == 202

    findings = (await async_client.get(f"/api/v1/matters/{matter_id}/findings")).json()
    cap_finding = next(f for f in findings if f["category"] == "limitation_of_liability")
    assert "CUSTOM FALLBACK" in cap_finding["metadata_json"]["proposed_redline"]
    assert cap_finding["metadata_json"]["playbook_name"] == "Strict Healthcare SaaS Playbook"

    # 4. Download Word (.docx) Redline export and verify valid OOXML + Track Changes
    docx_resp = await async_client.get(f"/api/v1/matters/{matter_id}/exports/redline.docx")
    assert docx_resp.status_code == 200
    assert (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        in docx_resp.headers["content-type"]
    )

    with zipfile.ZipFile(io.BytesIO(docx_resp.content)) as zf:
        names = set(zf.namelist())
        assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}.issubset(names)
        doc_xml = zf.read("word/document.xml").decode("utf-8")
        assert "<w:del " in doc_xml
        assert "<w:ins " in doc_xml
        assert "CUSTOM FALLBACK" in doc_xml
        assert "Does not constitute legal advice" in doc_xml

    # 5. Cleanup custom playbook
    del_pb = await async_client.delete(f"/api/v1/playbooks/{custom_pb['id']}")
    assert del_pb.status_code == 204
