import json
import re
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import yaml
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.metrics import metrics_registry
from app.core.telemetry import (
    configure_telemetry,
    extract_trace_context,
    get_tracer,
    inject_trace_headers,
)
from app.db.rls import load_rls_sql_statements, set_tenant_rls_context


async def test_telemetry_helpers_metrics_and_rls_context(raw_client: AsyncClient) -> None:
    assert configure_telemetry(Settings(otel_enabled=False)) is False

    tracer = get_tracer("ai_paralegal.test")
    with tracer.start_as_current_span("test.span"):
        carrier = inject_trace_headers({"x-custom": "1"})
        assert isinstance(carrier, dict)
        assert carrier["x-custom"] == "1"
        _ = extract_trace_context(
            {"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"}
        )

    metrics_registry.record_webhook_delivery("slack", True)
    metrics_registry.record_webhook_delivery("webhook", False)
    metrics_registry.record_retention_purge(2)

    metrics_resp = await raw_client.get("/metrics")
    assert metrics_resp.status_code == 200
    assert "x-request-id" in metrics_resp.headers
    text_body = metrics_resp.text
    assert (
        'ai_paralegal_webhook_deliveries_total{channel_type="slack",status="delivered"}'
        in text_body
    )
    assert (
        'ai_paralegal_webhook_deliveries_total{channel_type="webhook",status="failed"}'
        in text_body
    )
    assert "ai_paralegal_retention_purged_documents_total" in text_body

    # SQLite session returns False cleanly for RLS setter
    sqlite_session = MagicMock()
    sqlite_session.info = {}
    sqlite_bind = MagicMock()
    sqlite_bind.dialect.name = "sqlite"
    sqlite_session.get_bind.return_value = sqlite_bind
    assert await set_tenant_rls_context(sqlite_session, uuid.uuid4()) is False

    # PostgreSQL session binds GUC + attaches after_begin listener
    pg_session = MagicMock()
    pg_session.info = {}
    pg_session.sync_session = Session()
    pg_bind = MagicMock()
    pg_bind.dialect.name = "postgresql"
    pg_session.get_bind.return_value = pg_bind
    pg_session.execute = AsyncMock()
    org_id = uuid.uuid4()
    assert await set_tenant_rls_context(pg_session, org_id) is True
    assert pg_session.info.get("_rls_listener_attached") is True
    pg_session.execute.assert_awaited_once()
    assert pg_session.info["tenant_organization_id"] == str(org_id)


def test_deploy_manifests_monitoring_and_helm_valid() -> None:
    repo_root = Path(__file__).resolve().parents[2]

    # 1. RLS SQL script parses into non-empty statements covering core tenant tables
    stmts = load_rls_sql_statements()
    assert len(stmts) >= 12
    joined_sql = "\n".join(stmts)
    for table in (
        "matters",
        "documents",
        "document_chunks",
        "extracted_findings",
        "audit_logs",
        "playbooks",
        "webhook_subscriptions",
    ):
        assert "ENABLE ROW LEVEL SECURITY" in joined_sql
        assert f"{table} FORCE ROW LEVEL SECURITY" in joined_sql

    # 2. Raw Kubernetes manifests parse cleanly and harden API/Worker/Frontend pods
    k8s_files = list((repo_root / "deploy" / "k8s").glob("*.yaml"))
    assert len(k8s_files) >= 8
    for kf in k8s_files:
        docs = [d for d in yaml.safe_load_all(kf.read_text(encoding="utf-8")) if d is not None]
        assert len(docs) >= 1

    api_docs = list(
        yaml.safe_load_all((repo_root / "deploy" / "k8s" / "api-deployment.yaml").read_text())
    )
    api_dep = api_docs[0]
    assert api_dep["spec"]["template"]["spec"]["securityContext"]["runAsNonRoot"] is True
    container = api_dep["spec"]["template"]["spec"]["containers"][0]
    assert container["livenessProbe"]["httpGet"]["path"] == "/api/v1/health/live"
    assert container["readinessProbe"]["httpGet"]["path"] == "/api/v1/health/ready"

    # 3. Monitoring PrometheusRule, Grafana JSON, and OTel collector config match exported metrics
    metrics_registry.record_http_request("GET", "/api/v1/matters", 200, 0.01)
    metrics_registry.record_llm_call(
        provider="mock",
        model_name="mock-legal-deterministic-v1",
        task_kind="contract_review",
        status="success",
        latency_ms=1,
    )
    metrics_registry.record_pii_redaction("gemini", {"email": 1})
    metrics_registry.record_ocr_page("mock", True)
    metrics_registry.record_grounding_failure(1)
    metrics_registry.record_webhook_delivery("slack", True)
    metrics_registry.record_retention_purge(1)
    prom_text = metrics_registry.render_prometheus()

    alerts_text = (repo_root / "deploy" / "monitoring" / "prometheus-alerts.yaml").read_text(
        encoding="utf-8"
    )
    alerts_doc = yaml.safe_load(alerts_text)
    assert alerts_doc["kind"] == "PrometheusRule"

    dashboard_text = (repo_root / "deploy" / "monitoring" / "grafana-dashboard.json").read_text(
        encoding="utf-8"
    )
    dashboard = json.loads(dashboard_text)
    assert len(dashboard["panels"]) >= 6

    referenced_metrics = set(
        re.findall(r"ai_paralegal_[a-z0-9_]+", alerts_text + "\n" + dashboard_text)
    )
    assert len(referenced_metrics) >= 7
    for metric_name in referenced_metrics:
        assert metric_name in prom_text, f"Metric '{metric_name}' missing from render_prometheus()"

    otel_cfg = yaml.safe_load(
        (repo_root / "deploy" / "monitoring" / "otel-collector-config.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert "traces" in otel_cfg["service"]["pipelines"]

    # 4. Helm chart Chart.yaml, values.yaml, templates, and CI workflows validate cleanly
    helm_dir = repo_root / "deploy" / "helm" / "ai-paralegal"
    chart = yaml.safe_load((helm_dir / "Chart.yaml").read_text(encoding="utf-8"))
    values = yaml.safe_load((helm_dir / "values.yaml").read_text(encoding="utf-8"))
    assert chart["name"] == "ai-paralegal"
    assert values["beat"]["enabled"] is True

    expected_templates = (
        "_helpers.tpl",
        "configmap.yaml",
        "secret.yaml",
        "api-deployment.yaml",
        "worker-deployment.yaml",
        "frontend-deployment.yaml",
        "ingress.yaml",
        "networkpolicy.yaml",
        "servicemonitor.yaml",
        "NOTES.txt",
    )
    for tpl in expected_templates:
        assert (helm_dir / "templates" / tpl).is_file()

    for wf in ("ci.yml", "live-evals.yml"):
        parsed_wf = yaml.safe_load(
            (repo_root / ".github" / "workflows" / wf).read_text(encoding="utf-8")
        )
        assert "jobs" in parsed_wf
