import json
from unittest.mock import MagicMock

import httpx
import pytest
from httpx import AsyncClient

from app.storage.s3 import S3StorageBackend


async def test_s3_sse_kms_encryption_and_presigned_url() -> None:
    mock_boto = MagicMock()
    mock_boto.generate_presigned_url.return_value = "https://s3.example.com/signed?X-Amz-Sig=abc"

    s3 = S3StorageBackend(
        bucket="paralegal-vault",
        kms_key_id="arn:aws:kms:us-east-1:123456:key/abc",
        client=mock_boto,
    )
    key = await s3.put_bytes(
        "matters/m1/contract.pdf", b"%PDF-1.4\n...", content_type="application/pdf"
    )
    assert key == "matters/m1/contract.pdf"
    mock_boto.put_object.assert_called_once()
    call_kwargs = mock_boto.put_object.call_args.kwargs
    assert call_kwargs["ServerSideEncryption"] == "aws:kms"
    assert call_kwargs["SSEKMSKeyId"] == "arn:aws:kms:us-east-1:123456:key/abc"

    url = await s3.generate_presigned_url(key, expires_in=120, download_filename="contract.pdf")
    assert url == "https://s3.example.com/signed?X-Amz-Sig=abc"
    mock_boto.generate_presigned_url.assert_called_once()


async def test_dms_import_webhooks_word_addin_and_eval_feedback_export(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1. Create matter
    matter_resp = await async_client.post(
        "/api/v1/matters",
        json={
            "title": "Integrations & Feedback Matter",
            "client_name": "Northstar Legal",
            "practice_area": "Commercial Contracts",
        },
    )
    assert matter_resp.status_code == 201
    matter_id = matter_resp.json()["id"]

    # 2. Discover DMS providers & import sample_msa.txt via mock_dms
    provs_resp = await async_client.get("/api/v1/integrations/dms/providers")
    assert provs_resp.status_code == 200
    assert any(p["provider"] == "mock_dms" and p["configured"] for p in provs_resp.json())

    files_resp = await async_client.get("/api/v1/integrations/dms/mock_dms/files")
    assert files_resp.status_code == 200
    assert any(f["name"] == "sample_msa.txt" for f in files_resp.json())

    imp_resp = await async_client.post(
        f"/api/v1/matters/{matter_id}/integrations/dms/import",
        json={"provider": "mock_dms", "external_id": "sample_msa.txt", "document_type": "contract"},
    )
    assert imp_resp.status_code == 202
    doc_id = imp_resp.json()["document"]["id"]
    assert imp_resp.json()["document"]["metadata_json"]["dms_source_provider"] == "mock_dms"

    dl_url_resp = await async_client.get(f"/api/v1/documents/{doc_id}/download-url")
    assert dl_url_resp.status_code == 200
    assert dl_url_resp.json()["mode"] == "api_proxy"

    # 3. Webhook SSRF protection + Slack webhook registration & HMAC delivery
    ssrf_resp = await async_client.post(
        "/api/v1/integrations/webhooks",
        json={
            "name": "Blocked Metadata IP",
            "channel_type": "webhook",
            "target_url": "http://169.254.169.254/latest/meta-data/",
        },
    )
    assert ssrf_resp.status_code == 400

    wh_resp = await async_client.post(
        "/api/v1/integrations/webhooks",
        json={
            "name": "Partner Escalations Slack",
            "channel_type": "slack",
            "target_url": "https://hooks.slack.example.com/services/T000/B000/XXXX",
            "signing_secret": "super-webhook-secret",
            "matter_id": matter_id,
            "events": ["finding.escalated", "webhook.test"],
        },
    )
    assert wh_resp.status_code == 201
    wh_id = wh_resp.json()["id"]

    sent_webhooks: list[dict[str, object]] = []

    class _FakeWebhookClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "_FakeWebhookClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(
            self, url: str, *, content: bytes, headers: dict[str, str]
        ) -> httpx.Response:
            sent_webhooks.append({"url": url, "content": content, "headers": headers})
            return httpx.Response(status_code=200, request=httpx.Request("POST", url))

    monkeypatch.setattr("app.services.integrations.webhooks.validate_webhook_url", lambda u, **_: u)
    monkeypatch.setattr("app.services.integrations.webhooks.httpx.AsyncClient", _FakeWebhookClient)

    test_wh = await async_client.post(f"/api/v1/integrations/webhooks/{wh_id}/test")
    assert test_wh.status_code == 200
    assert test_wh.json()["delivered"] is True

    # Trigger contract review which escalates high-risk findings & notifies Slack webhook
    rev_resp = await async_client.post(
        f"/api/v1/matters/{matter_id}/analyses/contract-review",
        json={"provider": "mock"},
    )
    assert rev_resp.status_code == 202

    assert len(sent_webhooks) >= 2
    last_headers = sent_webhooks[-1]["headers"]
    assert isinstance(last_headers, dict)
    assert str(last_headers["X-AI-Paralegal-Signature"]).startswith("sha256=")
    assert last_headers["X-AI-Paralegal-Event"] == "finding.escalated"

    # 4. Word Office.js redline payload + Attorney feedback eval dataset export
    word_resp = await async_client.get(f"/api/v1/matters/{matter_id}/redlines/word-payload")
    assert word_resp.status_code == 200
    word_data = word_resp.json()
    assert len(word_data["items"]) >= 1

    finding_id = word_data["items"][0]["finding_id"]
    approve_resp = await async_client.patch(
        f"/api/v1/findings/{finding_id}/review",
        json={
            "review_status": "approved",
            "edited_summary": "Attorney-verified summary for regression training.",
            "reviewer_notes": "Confirmed mutual cap missing.",
        },
    )
    assert approve_resp.status_code == 200

    fb_resp = await async_client.get(f"/api/v1/matters/{matter_id}/exports/eval-dataset")
    assert fb_resp.status_code == 200
    fb_json = json.loads(fb_resp.text)
    assert fb_json["approved_count"] == 1
    assert len(fb_json["contract_feedback_cases"]) == 1
    assert (
        fb_json["contract_feedback_cases"][0]["attorney_summary"]
        == "Attorney-verified summary for regression training."
    )
