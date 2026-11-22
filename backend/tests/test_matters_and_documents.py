from httpx import AsyncClient

from tests.conftest import SAMPLES_DIR, build_sample_docx, build_sample_pdf


async def test_matter_crud_and_multi_format_ingestion(async_client: AsyncClient) -> None:
    # 1. Create matter
    create_resp = await async_client.post(
        "/api/v1/matters",
        json={
            "title": "Meridian v. Apex Cloud Contract Dispute",
            "client_name": "Meridian Legal Group LLP",
            "practice_area": "Commercial Litigation",
            "jurisdiction": "Delaware",
            "description": "Review MSA terms and billing communications.",
        },
    )
    assert create_resp.status_code == 201
    matter = create_resp.json()
    matter_id = matter["id"]
    assert matter["matter_number"].startswith("MAT-")

    # 2. List & update matter
    list_resp = await async_client.get("/api/v1/matters")
    assert list_resp.status_code == 200
    assert any(m["id"] == matter_id for m in list_resp.json())

    patch_resp = await async_client.patch(
        f"/api/v1/matters/{matter_id}",
        json={"jurisdiction": "New York"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["jurisdiction"] == "New York"

    # 3. Upload TXT contract (sample_msa.txt)
    msa_bytes = (SAMPLES_DIR / "sample_msa.txt").read_bytes()
    txt_resp = await async_client.post(
        f"/api/v1/matters/{matter_id}/documents",
        files={"file": ("sample_msa.txt", msa_bytes, "text/plain")},
        data={"document_type": "contract"},
    )
    assert txt_resp.status_code == 202
    txt_doc = txt_resp.json()["document"]
    assert txt_doc["ingestion_status"] == "indexed"
    assert txt_doc["chunk_count"] >= 4

    chunks_resp = await async_client.get(f"/api/v1/documents/{txt_doc['id']}/chunks")
    assert chunks_resp.status_code == 200
    chunks = chunks_resp.json()
    assert len(chunks) == txt_doc["chunk_count"]
    assert any("SECTION 6. INDEMNIFICATION" in (c["section_heading"] or "") for c in chunks)

    # 4. Upload EML thread (sample_discovery_emails.eml)
    eml_bytes = (SAMPLES_DIR / "sample_discovery_emails.eml").read_bytes()
    eml_resp = await async_client.post(
        f"/api/v1/matters/{matter_id}/documents",
        files={"file": ("sample_discovery_emails.eml", eml_bytes, "message/rfc822")},
        data={"document_type": "email"},
    )
    assert eml_resp.status_code == 202
    assert eml_resp.json()["document"]["ingestion_status"] == "indexed"

    # 5. Upload real PDF (2 pages) and verify page provenance
    pdf_bytes = build_sample_pdf(
        [
            "SECTION 1. SCOPE\nThe Vendor shall deliver cloud hosting services.",
            "SECTION 2. LIMITATION OF LIABILITY\nTotal liability is capped at $50,000.",
        ]
    )
    pdf_resp = await async_client.post(
        f"/api/v1/matters/{matter_id}/documents",
        files={"file": ("amendment.pdf", pdf_bytes, "application/pdf")},
        data={"document_type": "contract"},
    )
    assert pdf_resp.status_code == 202
    pdf_doc = pdf_resp.json()["document"]
    assert pdf_doc["ingestion_status"] == "indexed"
    assert pdf_doc["page_count"] == 2

    pdf_chunks = (await async_client.get(f"/api/v1/documents/{pdf_doc['id']}/chunks")).json()
    assert {c["page_number"] for c in pdf_chunks} == {1, 2}

    # 6. Upload real DOCX and verify section heading provenance
    docx_bytes = build_sample_docx(
        [
            ("Heading1", "Confidentiality Addendum"),
            (None, "Confidential information shall remain protected for five (5) years."),
        ]
    )
    docx_resp = await async_client.post(
        f"/api/v1/matters/{matter_id}/documents",
        files={
            "file": (
                "nda_addendum.docx",
                docx_bytes,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        data={"document_type": "contract"},
    )
    assert docx_resp.status_code == 202
    docx_doc = docx_resp.json()["document"]
    assert docx_doc["ingestion_status"] == "indexed"
    docx_chunks = (await async_client.get(f"/api/v1/documents/{docx_doc['id']}/chunks")).json()
    assert docx_chunks[0]["section_heading"] == "Confidentiality Addendum"

    # 7. Upload Markdown transcript & test reprocess + delete
    md_resp = await async_client.post(
        f"/api/v1/matters/{matter_id}/documents",
        files={
            "file": (
                "deposition_notes.md",
                b"# Deposition Summary\n\n[00:01:10] COUNSEL: Did you review Section 7?",
                "text/markdown",
            )
        },
        data={"document_type": "general"},
    )
    assert md_resp.status_code == 202
    md_id = md_resp.json()["document"]["id"]

    reproc = await async_client.post(f"/api/v1/documents/{md_id}/reprocess")
    assert reproc.status_code == 200
    assert reproc.json()["ingestion_status"] == "indexed"

    del_resp = await async_client.delete(f"/api/v1/documents/{md_id}")
    assert del_resp.status_code == 204

    get_deleted = await async_client.get(f"/api/v1/documents/{md_id}")
    assert get_deleted.status_code == 404
