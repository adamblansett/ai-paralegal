-- PostgreSQL Row-Level Security (RLS) policy pack for multi-tenant isolation.
-- Note: PostgreSQL SUPERUSER and BYPASSRLS roles bypass RLS even when FORCE ROW LEVEL SECURITY
-- is enabled; production application traffic MUST connect using a NOSUPERUSER NOBYPASSRLS role.
-- Authenticated backend requests set the transaction-local tenant context automatically via:
--   SELECT set_config('app.current_organization_id', '<org-uuid>', true);

ALTER TABLE matters ENABLE ROW LEVEL SECURITY;
ALTER TABLE matters FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS matters_tenant_isolation ON matters;
CREATE POLICY matters_tenant_isolation ON matters
    USING (
        current_setting('app.current_organization_id', true) IS NULL
        OR current_setting('app.current_organization_id', true) = ''
        OR organization_id = current_setting('app.current_organization_id', true)::uuid
    )
    WITH CHECK (
        current_setting('app.current_organization_id', true) IS NULL
        OR current_setting('app.current_organization_id', true) = ''
        OR organization_id = current_setting('app.current_organization_id', true)::uuid
    );

ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS documents_tenant_isolation ON documents;
CREATE POLICY documents_tenant_isolation ON documents
    USING (
        EXISTS (
            SELECT 1 FROM matters m
            WHERE m.id = documents.matter_id
              AND (
                  current_setting('app.current_organization_id', true) IS NULL
                  OR current_setting('app.current_organization_id', true) = ''
                  OR m.organization_id = current_setting('app.current_organization_id', true)::uuid
              )
        )
    );

ALTER TABLE document_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_chunks FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS document_chunks_tenant_isolation ON document_chunks;
CREATE POLICY document_chunks_tenant_isolation ON document_chunks
    USING (
        EXISTS (
            SELECT 1 FROM matters m
            WHERE m.id = document_chunks.matter_id
              AND (
                  current_setting('app.current_organization_id', true) IS NULL
                  OR current_setting('app.current_organization_id', true) = ''
                  OR m.organization_id = current_setting('app.current_organization_id', true)::uuid
              )
        )
    );

ALTER TABLE extracted_findings ENABLE ROW LEVEL SECURITY;
ALTER TABLE extracted_findings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS findings_tenant_isolation ON extracted_findings;
CREATE POLICY findings_tenant_isolation ON extracted_findings
    USING (
        EXISTS (
            SELECT 1 FROM matters m
            WHERE m.id = extracted_findings.matter_id
              AND (
                  current_setting('app.current_organization_id', true) IS NULL
                  OR current_setting('app.current_organization_id', true) = ''
                  OR m.organization_id = current_setting('app.current_organization_id', true)::uuid
              )
        )
    );

ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS audit_logs_tenant_isolation ON audit_logs;
CREATE POLICY audit_logs_tenant_isolation ON audit_logs
    USING (
        EXISTS (
            SELECT 1 FROM matters m
            WHERE m.id = audit_logs.matter_id
              AND (
                  current_setting('app.current_organization_id', true) IS NULL
                  OR current_setting('app.current_organization_id', true) = ''
                  OR m.organization_id = current_setting('app.current_organization_id', true)::uuid
              )
        )
    );

ALTER TABLE playbooks ENABLE ROW LEVEL SECURITY;
ALTER TABLE playbooks FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS playbooks_tenant_isolation ON playbooks;
CREATE POLICY playbooks_tenant_isolation ON playbooks
    USING (
        organization_id IS NULL
        OR current_setting('app.current_organization_id', true) IS NULL
        OR current_setting('app.current_organization_id', true) = ''
        OR organization_id = current_setting('app.current_organization_id', true)::uuid
    );

ALTER TABLE webhook_subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE webhook_subscriptions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS webhooks_tenant_isolation ON webhook_subscriptions;
CREATE POLICY webhooks_tenant_isolation ON webhook_subscriptions
    USING (
        organization_id IS NULL
        OR current_setting('app.current_organization_id', true) IS NULL
        OR current_setting('app.current_organization_id', true) = ''
        OR organization_id = current_setting('app.current_organization_id', true)::uuid
    );
