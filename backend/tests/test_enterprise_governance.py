import uuid

from httpx import AsyncClient
from sqlalchemy import update

from app.api.v1.auth import map_oidc_groups_to_role
from app.core.config import Settings
from app.db.session import get_session_factory
from app.models.audit_log import AuditLog
from tests.conftest import register_and_login


def test_oidc_group_to_role_mapping() -> None:
    settings = Settings()
    assert (
        map_oidc_groups_to_role({"groups": ["all-staff", "legal-partners"]}, settings) == "partner"
    )
    assert map_oidc_groups_to_role({"groups": ["legal-paralegals"]}, settings) == "paralegal"
    assert map_oidc_groups_to_role({"groups": ["legal-auditors"]}, settings) == "auditor"
    assert map_oidc_groups_to_role({"groups": ["unmapped-group"]}, settings) == "associate"


async def test_multi_tenant_isolation_legal_hold_and_tamper_evident_audit_chain(
    raw_client: AsyncClient,
    async_client: AsyncClient,
) -> None:
    # 1. Create matter inside default firm tenant
    matter_resp = await async_client.post(
        "/api/v1/matters",
        json={
            "title": "Tenant & Governance Matter",
            "client_name": "Beacon Holdings",
            "practice_area": "Litigation",
        },
    )
    assert matter_resp.status_code == 201
    matter_id = matter_resp.json()["id"]

    # 2. Rival firm Partner MUST NOT see or access default firm matter (tenant isolation)
    rival_token, _ = await register_and_login(
        raw_client,
        email="partner@rival-law.example.com",
        full_name="Rival Partner",
        role="partner",
    )
    # Use /auth/register directly with organization_slug for a distinct tenant
    rival_reg = await raw_client.post(
        "/api/v1/auth/register",
        json={
            "email": "isolated.partner@rival.example.com",
            "full_name": "Isolated Tenant Partner",
            "password": "StrongTestPass123!",
            "role": "partner",
            "organization_slug": "rival-firm",
            "organization_name": "Rival LLP",
        },
    )
    assert rival_reg.status_code == 201
    rival_org_token = rival_reg.json()["access_token"]
    _ = rival_token

    rival_list = await raw_client.get(
        "/api/v1/matters",
        headers={"Authorization": f"Bearer {rival_org_token}"},
    )
    assert rival_list.status_code == 200
    assert all(m["id"] != matter_id for m in rival_list.json())

    rival_get = await raw_client.get(
        f"/api/v1/matters/{matter_id}",
        headers={"Authorization": f"Bearer {rival_org_token}"},
    )
    assert rival_get.status_code == 403

    # 3. Upload document, activate Legal Hold, verify 409 delete protection on doc & matter
    doc_resp = await async_client.post(
        f"/api/v1/matters/{matter_id}/documents",
        files={"file": ("evidence.txt", b"Key contractual evidence.", "text/plain")},
        data={"document_type": "contract"},
    )
    assert doc_resp.status_code == 202
    doc_id = doc_resp.json()["document"]["id"]

    hold_on = await async_client.post(
        f"/api/v1/matters/{matter_id}/legal-hold",
        json={"active": True, "reason": "Pending federal preservation order"},
    )
    assert hold_on.status_code == 200
    assert hold_on.json()["legal_hold_active"] is True

    blocked_doc_del = await async_client.delete(f"/api/v1/documents/{doc_id}")
    assert blocked_doc_del.status_code == 409

    blocked_matter_del = await async_client.delete(f"/api/v1/matters/{matter_id}")
    assert blocked_matter_del.status_code == 409

    # 4. Audit hash chain verification passes cleanly
    verify_ok = await async_client.get(f"/api/v1/matters/{matter_id}/audit-logs/verify")
    assert verify_ok.status_code == 200
    verify_data = verify_ok.json()
    assert verify_data["verified"] is True
    assert verify_data["total_entries"] >= 4
    assert verify_data["latest_hash"]

    # Simulate database-level row tampering and verify tamper detection catches exact sequence
    async with get_session_factory()() as session:
        await session.execute(
            update(AuditLog)
            .where(AuditLog.matter_id == uuid.UUID(matter_id), AuditLog.sequence_number == 2)
            .values(notes="TAMPERED HISTORY")
        )
        await session.commit()

    verify_broken = await async_client.get(f"/api/v1/matters/{matter_id}/audit-logs/verify")
    assert verify_broken.status_code == 200
    broken_data = verify_broken.json()
    assert broken_data["verified"] is False
    assert broken_data["broken_at_sequence"] == 2

    # 5. Release legal hold and verify delete now succeeds
    hold_off = await async_client.post(
        f"/api/v1/matters/{matter_id}/legal-hold",
        json={"active": False, "reason": "Preservation order lifted"},
    )
    assert hold_off.status_code == 200
    assert hold_off.json()["legal_hold_active"] is False

    ok_doc_del = await async_client.delete(f"/api/v1/documents/{doc_id}")
    assert ok_doc_del.status_code == 204
