# AI Paralegal — Production Operations Runbook

## 1. Zero-Downtime Database Migrations & `pgvector` Setup

1. Ensure the `vector` extension is enabled on PostgreSQL (`CREATE EXTENSION IF NOT EXISTS vector;`).
2. Before rolling new API/Worker containers, execute schema migrations from an ephemeral admin job/pod:
   ```bash
   cd backend
   alembic upgrade head
   ```
3. Verify schema revision is at `0005_legal_ops_mfa_walls`.

---

## 2. Bootstrapping PostgreSQL Row-Level Security (`NOSUPERUSER`)

PostgreSQL `SUPERUSER` and `BYPASSRLS` roles bypass RLS even with `FORCE ROW LEVEL SECURITY`.

1. Create a restricted application role for runtime traffic:
   ```sql
   CREATE ROLE paralegal_app LOGIN PASSWORD 'REPLACE_WITH_STRONG_SECRET'
     NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
   GRANT CONNECT ON DATABASE ai_paralegal TO paralegal_app;
   GRANT USAGE ON SCHEMA public TO paralegal_app;
   GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO paralegal_app;
   ALTER DEFAULT PRIVILEGES IN SCHEMA public
     GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO paralegal_app;
   ```
2. Apply tenant RLS policies as DB admin:
   ```bash
   psql "$ADMIN_DATABASE_URL" -f deploy/sql/rls_policies.sql
   ```
3. Point runtime `DATABASE_URL` in API/Worker secrets at `paralegal_app`. Authenticated API requests automatically execute `set_config('app.current_organization_id', <org_uuid>, true)` per transaction.

---

## 3. Enterprise Identity, TOTP MFA & SCIM 2.0 Provisioning

- **OIDC JWKS SSO:** Set `OIDC_ENABLED=true`, `OIDC_ISSUER_URL`, `OIDC_CLIENT_ID`, and `OIDC_ROLE_MAPPING_JSON`. Tokens are verified against `jwks_uri` (RS256/RS384/RS512 or ES256–ES512), refreshing cached JWKS once on key rotation (`kid` miss) and syncing firm roles on login.
- **TOTP MFA:** Users enroll via **Enterprise Admin & Integrations -> Org & DMS Status -> Enable TOTP MFA** (`/api/v1/auth/mfa/enroll` + `/api/v1/auth/mfa/verify`). Subsequent password logins require `totp_code`.
- **SCIM 2.0 Directory Sync:** Set `SCIM_ENABLED=true` and `SCIM_BEARER_TOKEN` (minimum 24 chars in production). Point Okta/Entra ID SCIM base URL to `https://<host>/api/v1/scim/v2`. Deactivating or deleting a user via SCIM immediately revokes all active refresh tokens.

---

## 4. Tamper-Evident Audit Chain Incident Response

1. Verify any matter's hash chain via UI (**Review Queue & Audit Trail -> Verify Hash Chain**) or API:
   ```bash
   curl -H "Authorization: Bearer $TOKEN" \
     "https://$HOST/api/v1/matters/$MATTER_ID/audit-logs/verify"
   ```
2. If `"verified": false`, capture `broken_at_sequence`, export raw `audit_logs` rows alongside PostgreSQL WAL/Point-In-Time Recovery (PITR) logs, preserve the matter under Litigation Legal Hold (`POST /api/v1/matters/$MATTER_ID/legal-hold`), and escalate to Security & General Counsel.

---

## 5. Litigation Legal Hold & Retention Purge Worker

- Celery Beat triggers `ai_paralegal.retention_sweep` every `RETENTION_SWEEP_INTERVAL_HOURS` (default `24h`).
- Documents/matters with `legal_hold_active=true` are automatically skipped by retention sweeps (`skipped_legal_hold`) and return `409 Conflict` on manual `DELETE` requests.
- Partners can preview impact anytime via **Admin & Integrations -> Retention & Compliance -> Preview Expired (Dry Run)**.

---

## 6. Backup & KMS Restore Drill

- **PostgreSQL:** RDS automated backups retain 14 days encrypted with KMS CMK. Test restore quarterly into an isolated staging DB and run `alembic current` + `/audit-logs/verify` across sampled matters.
- **S3 Document Vault:** Object versioning is enabled with `aws:kms` SSE. Verify IAM IRSA permissions (`kms:Decrypt`, `s3:GetObject`) match `deploy/terraform/main.tf` before cutting over KMS key policies.
