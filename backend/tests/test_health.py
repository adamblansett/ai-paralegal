from httpx import AsyncClient


async def test_health_endpoint(raw_client: AsyncClient) -> None:
    resp = await raw_client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["database"] == "ok"
    assert data["default_provider"] == "mock"
    assert data["auth_enabled"] is True
    assert "ocr_backend" in data
    assert "ollama" in data["supported_models"]
    assert "llama-3.1-8b-instruct" in data["supported_models"]["ollama"]
    assert "gemini" in data["supported_models"]
    assert data["max_upload_size_mb"] == 25
    assert data["pii_cloud_redaction_enabled"] is True
    assert data["oidc_enabled"] is False
    assert "Does not constitute legal advice" in data["disclaimer"]
