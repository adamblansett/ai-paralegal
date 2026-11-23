export type LLMProviderName = 'mock' | 'ollama' | 'gemini';
export type ReviewStatus = 'pending_review' | 'approved' | 'flagged';
export type SeverityLevel = 'info' | 'low' | 'medium' | 'high' | 'critical';
export type VerificationStatus = 'verified_exact' | 'verified_fuzzy' | 'unverified';
export type UserRole = 'partner' | 'associate' | 'paralegal' | 'auditor';
export type AccessLevel = 'owner' | 'editor' | 'reviewer' | 'viewer';
export type PrivilegeStatus =
  | 'none'
  | 'attorney_client'
  | 'work_product'
  | 'confidential'
  | 'privileged_and_confidential';

export interface UserProfile {
  id: string;
  organization_id?: string | null;
  email: string;
  full_name: string;
  role: UserRole;
  is_active: boolean;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  refresh_token?: string | null;
  expires_in?: number;
  token_type: string;
  user: UserProfile;
}

export interface MatterMember {
  id: string;
  matter_id: string;
  user_id: string;
  user_email: string;
  user_full_name: string;
  user_role: UserRole;
  access_level: AccessLevel;
  created_at: string;
}

export interface PlaybookRule {
  clause_type: string;
  standard_position: string;
  fallback_position: string;
  red_flag_patterns: string[];
  required: boolean;
  severity_if_deviated: SeverityLevel;
}

export interface Playbook {
  id: string;
  name: string;
  practice_area: string;
  description: string | null;
  is_default: boolean;
  created_by_id: string | null;
  rules_json: PlaybookRule[];
  created_at: string;
  updated_at: string;
}

export interface HealthResponse {
  status: string;
  database: string;
  app_name: string;
  environment: string;
  default_provider: string;
  fallback_providers: string[];
  supported_models: Record<string, string[]>;
  storage_backend: string;
  task_execution_mode: string;
  auth_enabled: boolean;
  ocr_backend: string;
  tesseract_available: boolean;
  max_upload_size_mb?: number;
  pii_cloud_redaction_enabled?: boolean;
  oidc_enabled?: boolean;
  disclaimer: string;
}

export interface MatterStats {
  document_count: number;
  indexed_document_count: number;
  finding_count: number;
  pending_review_count: number;
  flagged_count: number;
  approved_count: number;
}

export interface Matter {
  id: string;
  organization_id?: string | null;
  matter_number: string;
  title: string;
  client_name: string;
  practice_area: string;
  jurisdiction: string | null;
  description: string | null;
  status: string;
  legal_hold_active?: boolean;
  legal_hold_reason?: string | null;
  legal_hold_set_at?: string | null;
  legal_hold_set_by?: string | null;
  retention_days?: number | null;
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  stats: MatterStats;
}

export interface MatterCreateInput {
  matter_number?: string;
  title: string;
  client_name: string;
  practice_area: string;
  jurisdiction?: string;
  description?: string;
}

export interface DocumentItem {
  id: string;
  matter_id: string;
  filename: string;
  content_type: string;
  document_type: string;
  storage_backend: string;
  storage_key: string;
  sha256_hash: string;
  file_size_bytes: number;
  ingestion_status: 'pending' | 'processing' | 'indexed' | 'failed';
  error_message: string | null;
  page_count: number;
  word_count: number;
  chunk_count: number;
  bates_start?: string | null;
  bates_end?: string | null;
  privilege_status?: PrivilegeStatus;
  privilege_reason?: string | null;
  pii_detected_count?: number;
  pii_summary_json?: Record<string, number>;
  legal_hold_active?: boolean;
  retention_until?: string | null;
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface BatesStampResponse {
  matter_id: string;
  prefix: string;
  stamped_count: number;
  next_number: number;
  documents: DocumentItem[];
}

export interface MatrixCell {
  severity: SeverityLevel | 'none';
  finding_count: number;
  review_status: ReviewStatus;
  escalated: boolean;
  finding_ids: string[];
  top_title?: string | null;
}

export interface MatrixDocumentRow {
  document_id: string;
  filename: string;
  bates_range: string | null;
  privilege_status: string;
  overall_risk: string;
  finding_count: number;
  escalated_count: number;
  cells: Record<string, MatrixCell>;
}

export interface PortfolioRiskMatrix {
  matter_id: string;
  categories: string[];
  rows: MatrixDocumentRow[];
  total_documents: number;
  total_findings: number;
  high_risk_documents: number;
}

export interface SourceViewerTarget {
  documentId: string;
  filename?: string | null;
  chunkId?: string | null;
  quote?: string | null;
  pageNumber?: number | null;
  paragraphIndex?: number | null;
  sectionHeading?: string | null;
}

export interface TelemetrySummary {
  http_requests_total: number;
  llm_calls_total: number;
  llm_calls_by_provider: Record<string, number>;
  llm_prompt_tokens_total: number;
  llm_completion_tokens_total: number;
  llm_estimated_cost_usd_total: number;
  pii_redactions_total: number;
  ocr_pages_processed_total: number;
  citation_grounding_failures_total: number;
}

export interface DocumentChunkItem {
  id: string;
  document_id: string;
  matter_id: string;
  chunk_index: number;
  content: string;
  page_number: number | null;
  paragraph_index: number | null;
  section_heading: string | null;
  char_start: number | null;
  char_end: number | null;
  token_count: number;
  metadata_json: Record<string, unknown>;
  created_at: string;
}

export interface DocumentUploadResponse {
  document: DocumentItem;
  task_id: string;
  ingestion_status: string;
}

export interface VerifiedCitation {
  chunk_id: string;
  chunk_ref: string | null;
  document_id: string | null;
  document_filename: string | null;
  page_number: number | null;
  paragraph_index: number | null;
  section_heading: string | null;
  char_start: number | null;
  char_end: number | null;
  quote: string;
  matched_excerpt: string | null;
  verification_status: VerificationStatus;
  match_score: number;
}

export interface ExtractedFinding {
  id: string;
  matter_id: string;
  document_id: string | null;
  review_task_id: string | null;
  finding_type: string;
  category: string;
  title: string;
  summary: string;
  edited_summary: string | null;
  severity: SeverityLevel;
  confidence: number;
  grounding_score: number;
  requires_escalation: boolean;
  escalation_reason: string | null;
  recommended_action: string | null;
  citations_json: VerifiedCitation[];
  metadata_json: Record<string, unknown>;
  review_status: ReviewStatus;
  reviewer_name: string | null;
  reviewer_notes: string | null;
  reviewed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ReviewTaskItem {
  id: string;
  matter_id: string;
  document_id: string | null;
  task_type: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  celery_task_id: string | null;
  requested_provider: string | null;
  requested_model: string | null;
  resolved_provider: string | null;
  resolved_model: string | null;
  input_params_json: Record<string, unknown>;
  result_json: Record<string, unknown>;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface SearchHit {
  chunk_id: string;
  document_id: string;
  document_filename: string;
  document_type: string;
  chunk_index: number;
  content: string;
  page_number: number | null;
  paragraph_index: number | null;
  section_heading: string | null;
  char_start: number | null;
  char_end: number | null;
  lexical_score: number;
  semantic_score: number;
  rrf_score: number;
}

export interface SearchResponse {
  matter_id: string;
  query: string;
  hits: SearchHit[];
}

export interface ResearchQueryResponse {
  task_id: string;
  finding_id: string | null;
  question: string;
  answer: string;
  key_points: string[];
  uncertainties: string[];
  missing_context: string[];
  confidence: number;
  grounding_score: number;
  requires_escalation: boolean;
  escalation_reason: string | null;
  citations: VerifiedCitation[];
  provider: string;
  model_name: string;
  disclaimer: string;
}

export interface AuditLogEntry {
  id: string;
  matter_id: string;
  document_id: string | null;
  review_task_id: string | null;
  finding_id: string | null;
  action: string;
  actor_type: string;
  actor_id: string;
  provider: string | null;
  model_name: string | null;
  prompt_template: string | null;
  prompt_version: string | null;
  prompt_text: string | null;
  retrieved_spans_json: Array<Record<string, unknown>>;
  response_metadata_json: Record<string, unknown>;
  token_usage_json: Record<string, unknown>;
  latency_ms: number | null;
  grounding_score: number | null;
  escalated: boolean;
  previous_state: string | null;
  new_state: string | null;
  notes: string | null;
  sequence_number?: number;
  previous_hash?: string | null;
  entry_hash?: string | null;
  created_at: string;
}

export interface AuditChainVerification {
  matter_id: string;
  total_entries: number;
  verified: boolean;
  broken_at_sequence: number | null;
  latest_hash: string | null;
  detail: string;
}

export interface DMSProviderInfo {
  provider: string;
  display_name: string;
  configured: boolean;
  description: string;
}

export interface DMSFileItem {
  external_id: string;
  name: string;
  content_type: string;
  document_type: string;
  size_bytes: number;
  modified_at: string;
  source_uri: string;
}

export type WebhookChannelType = 'webhook' | 'slack' | 'teams';

export interface WebhookSubscription {
  id: string;
  organization_id: string | null;
  matter_id: string | null;
  name: string;
  channel_type: WebhookChannelType;
  target_url: string;
  events_json: string[];
  is_active: boolean;
  created_at: string;
}

export interface WebhookCreateInput {
  name: string;
  channel_type: WebhookChannelType;
  target_url: string;
  signing_secret?: string;
  matter_id?: string | null;
  events?: string[];
}

export interface WebhookTestResult {
  delivered: boolean;
  status_code: number | null;
  error: string | null;
}

export interface OrganizationInfo {
  id: string;
  slug: string;
  name: string;
  retention_days: number;
  is_active: boolean;
  created_at: string;
  user_count: number;
  matter_count: number;
  webhook_count: number;
}

export interface RetentionSweepItem {
  document_id: string;
  matter_id: string;
  filename: string;
  action: 'purged' | 'eligible_for_purge' | 'skipped_legal_hold';
  reason: string;
  retention_until: string | null;
}

export interface RetentionSweepReport {
  organization_id: string | null;
  dry_run: boolean;
  as_of: string;
  evaluated_count: number;
  purged_count: number;
  eligible_count: number;
  skipped_legal_hold_count: number;
  items: RetentionSweepItem[];
}
