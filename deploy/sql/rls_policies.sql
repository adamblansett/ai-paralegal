-- Optional PostgreSQL Row-Level Security (RLS) policy pack for multi-tenant isolation.
-- Application transactions executing under a specific tenant set:
--   SET LOCAL app.current_organization_id = '<org-uuid>';

ALTER TABLE matters ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS matters_tenant_isolation ON matters;
CREATE POLICY matters_tenant_isolation ON matters
    USING (
        current_setting('app.current_organization_id', true) IS NULL
        OR current_setting('app.current_organization_id', true) = ''
        OR organization_id = current_setting('app.current_organization_id', true)::uuid
    );

ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
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
