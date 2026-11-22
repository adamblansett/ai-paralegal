from httpx import AsyncClient


async def test_health_endpoint(async_client: AsyncClient) -> None:
    resp = await async_client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["database"] == "ok"
    assert data["default_provider"] == "mock"
    assert "ollama" in data["supported_models"]
    assert "llama-3.1-8b-instruct" in data["supported_models"]["ollama"]
    assert "gemini" in data["supported_models"]
    assert "Does not constitute legal advice" in data["disclaimer"]
