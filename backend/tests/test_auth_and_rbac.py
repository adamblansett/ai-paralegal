from httpx import AsyncClient

from tests.conftest import SAMPLES_DIR, register_and_login


async def test_unauthenticated_requests_rejected_and_role_matter_isolation_enforced(
    raw_client: AsyncClient,
) -> None:
    # 1. Unauthenticated requests return 401
    unauth = await raw_client.get("/api/v1/matters")
    assert unauth.status_code == 401

    # 2. Seeded demo login + custom user registration
    assoc_a_token, assoc_a = await register_and_login(
        raw_client,
        email="alice.assoc@firm.example.com",
        full_name="Alice Associate",
        role="associate",
    )
    assoc_b_token, assoc_b = await register_and_login(
        raw_client,
        email="bob.assoc@firm.example.com",
        full_name="Bob Associate",
        role="associate",
    )
    para_token, para_user = await register_and_login(
        raw_client,
        email="pat.para@firm.example.com",
        full_name="Pat Paralegal",
        role="paralegal",
    )
    auditor_login = await raw_client.post(
        "/api/v1/auth/login",
        json={"email": "auditor@firm.example.com", "password": "ParalegalDemo2026!"},
    )
    assert auditor_login.status_code == 200
    auditor_token = auditor_login.json()["access_token"]

    # 3. Associate A creates a matter (automatically becomes matter owner)
    hdr_a = {"Authorization": f"Bearer {assoc_a_token}"}
    matter_resp = await raw_client.post(
        "/api/v1/matters",
        headers=hdr_a,
        json={
            "title": "Confidential M&A Target Review",
            "client_name": "Meridian Corp",
            "practice_area": "M&A",
        },
    )
    assert matter_resp.status_code == 201
    matter_id = matter_resp.json()["id"]

    # Upload & review MSA as Associate A
    msa_bytes = (SAMPLES_DIR / "sample_msa.txt").read_bytes()
    up_resp = await raw_client.post(
        f"/api/v1/matters/{matter_id}/documents",
        headers=hdr_a,
        files={"file": ("sample_msa.txt", msa_bytes, "text/plain")},
        data={"document_type": "contract"},
    )
    assert up_resp.status_code == 202
    rev_resp = await raw_client.post(
        f"/api/v1/matters/{matter_id}/analyses/contract-review",
        headers=hdr_a,
        json={},
    )
    assert rev_resp.status_code == 202

    findings = (
        await raw_client.get(f"/api/v1/matters/{matter_id}/findings", headers=hdr_a)
    ).json()
    finding_id = findings[0]["id"]

    # 4. Associate B cannot see or access Associate A's matter (403)
    hdr_b = {"Authorization": f"Bearer {assoc_b_token}"}
    b_matters = (await raw_client.get("/api/v1/matters", headers=hdr_b)).json()
    assert all(m["id"] != matter_id for m in b_matters)
    b_get = await raw_client.get(f"/api/v1/matters/{matter_id}", headers=hdr_b)
    assert b_get.status_code == 403

    # 5. Associate A assigns Paralegal to matter -> Paralegal can view & flag, NOT approve
    hdr_para = {"Authorization": f"Bearer {para_token}"}
    add_para = await raw_client.post(
        f"/api/v1/matters/{matter_id}/members",
        headers=hdr_a,
        json={"user_id": para_user["id"], "access_level": "editor"},
    )
    assert add_para.status_code == 201

    para_flag = await raw_client.patch(
        f"/api/v1/findings/{finding_id}/review",
        headers=hdr_para,
        json={"review_status": "flagged", "reviewer_notes": "Needs partner eyes"},
    )
    assert para_flag.status_code == 200
    assert para_flag.json()["reviewer_name"] == "Pat Paralegal (paralegal)"

    para_approve = await raw_client.patch(
        f"/api/v1/findings/{finding_id}/review",
        headers=hdr_para,
        json={"review_status": "approved"},
    )
    assert para_approve.status_code == 403

    # 6. Auditor can read matters and audit logs, but gets 403 on all writes
    hdr_aud = {"Authorization": f"Bearer {auditor_token}"}
    aud_logs = await raw_client.get(f"/api/v1/matters/{matter_id}/audit-logs", headers=hdr_aud)
    assert aud_logs.status_code == 200
    assert any("alice.assoc@firm.example.com" in log["actor_id"] for log in aud_logs.json())

    aud_create = await raw_client.post(
        "/api/v1/matters",
        headers=hdr_aud,
        json={"title": "Blocked", "client_name": "Test"},
    )
    assert aud_create.status_code == 403

    aud_flag = await raw_client.patch(
        f"/api/v1/findings/{finding_id}/review",
        headers=hdr_aud,
        json={"review_status": "flagged"},
    )
    assert aud_flag.status_code == 403

    # 7. Associate A grants Viewer access to Associate B -> B can read, not write
    add_b = await raw_client.post(
        f"/api/v1/matters/{matter_id}/members",
        headers=hdr_a,
        json={"user_id": assoc_b["id"], "access_level": "viewer"},
    )
    assert add_b.status_code == 201
    assert (await raw_client.get(f"/api/v1/matters/{matter_id}", headers=hdr_b)).status_code == 200
    b_write = await raw_client.post(
        f"/api/v1/matters/{matter_id}/analyses/contract-review",
        headers=hdr_b,
        json={},
    )
    assert b_write.status_code == 403
    _ = assoc_a
