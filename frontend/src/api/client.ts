import type {
  AccessLevel,
  AuditChainVerification,
  AuditLogEntry,
  BatesStampResponse,
  ConflictCheckResponse,
  DMSFileItem,
  DMSProviderInfo,
  DocumentChunkItem,
  DocumentComparisonResponse,
  DocumentItem,
  DocumentUploadResponse,
  EthicalWallEntry,
  ExtractedFinding,
  HealthResponse,
  LLMProviderName,
  Matter,
  MatterCreateInput,
  MatterMember,
  MatterObligation,
  MFAEnrollResponse,
  ObligationAlertResult,
  ObligationStatus,
  OrganizationInfo,
  Playbook,
  PlaybookRule,
  PortfolioRiskMatrix,
  PrivilegeStatus,
  ResearchQueryResponse,
  RetentionSweepReport,
  ReviewStatus,
  ReviewTaskItem,
  SearchResponse,
  TelemetrySummary,
  TokenResponse,
  UserProfile,
  UserRole,
  WebhookCreateInput,
  WebhookSubscription,
  WebhookTestResult,
} from '../types/api';

const API_BASE = '/api/v1';
const TOKEN_STORAGE_KEY = 'ai_paralegal_token';
const REFRESH_TOKEN_STORAGE_KEY = 'ai_paralegal_refresh_token';

export const tokenStore = {
  get: (): string | null => window.localStorage.getItem(TOKEN_STORAGE_KEY),
  set: (token: string): void => window.localStorage.setItem(TOKEN_STORAGE_KEY, token),
  getRefresh: (): string | null => window.localStorage.getItem(REFRESH_TOKEN_STORAGE_KEY),
  setRefresh: (token: string): void => window.localStorage.setItem(REFRESH_TOKEN_STORAGE_KEY, token),
  clear: (): void => {
    window.localStorage.removeItem(TOKEN_STORAGE_KEY);
    window.localStorage.removeItem(REFRESH_TOKEN_STORAGE_KEY);
  },
};

let refreshPromise: Promise<string | null> | null = null;

async function attemptTokenRefresh(): Promise<string | null> {
  const refresh = tokenStore.getRefresh();
  if (!refresh) return null;

  if (!refreshPromise) {
    refreshPromise = (async () => {
      try {
        const res = await fetch(`${API_BASE}/auth/refresh`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: refresh }),
        });
        if (!res.ok) {
          tokenStore.clear();
          return null;
        }
        const data = (await res.json()) as TokenResponse;
        tokenStore.set(data.access_token);
        if (data.refresh_token) {
          tokenStore.setRefresh(data.refresh_token);
        }
        return data.access_token;
      } catch {
        tokenStore.clear();
        return null;
      } finally {
        refreshPromise = null;
      }
    })();
  }
  return refreshPromise;
}

async function apiRequest<T>(
  path: string,
  init?: RequestInit,
  canRetryRefresh = true
): Promise<T> {
  const token = tokenStore.get();
  const headers: Record<string, string> = {
    ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };

  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...headers,
      ...((init?.headers as Record<string, string> | undefined) ?? {}),
    },
  });

  if (
    response.status === 401 &&
    canRetryRefresh &&
    !path.startsWith('/auth/login') &&
    !path.startsWith('/auth/refresh') &&
    !path.startsWith('/auth/register')
  ) {
    const renewed = await attemptTokenRefresh();
    if (renewed) {
      return apiRequest<T>(path, init, false);
    }
  }

  if (!response.ok) {
    if (response.status === 401) {
      tokenStore.clear();
    }
    let detail = `HTTP ${response.status}`;
    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) detail = payload.detail;
    } catch {
      // ignore non-JSON body
    }
    throw new Error(detail);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

function authQuerySuffix(leading = '&'): string {
  const token = tokenStore.get();
  return token ? `${leading}token=${encodeURIComponent(token)}` : '';
}

export const api = {
  health: () => apiRequest<HealthResponse>('/health'),
  telemetrySummary: () => apiRequest<TelemetrySummary>('/telemetry/summary'),

  // Auth & RBAC
  authProviders: () =>
    apiRequest<{
      local_enabled: boolean;
      oidc_enabled: boolean;
      oidc_issuer_url: string | null;
      oidc_client_id: string | null;
    }>('/auth/providers'),
  login: (email: string, password: string, totpCode?: string) =>
    apiRequest<TokenResponse>('/auth/login', {
      method: 'POST',
      body: JSON.stringify(
        totpCode ? { email, password, totp_code: totpCode } : { email, password }
      ),
    }),
  enrollMfa: () =>
    apiRequest<MFAEnrollResponse>('/auth/mfa/enroll', {
      method: 'POST',
    }),
  verifyMfa: (code: string) =>
    apiRequest<UserProfile>('/auth/mfa/verify', {
      method: 'POST',
      body: JSON.stringify({ code }),
    }),
  disableMfa: (code: string) =>
    apiRequest<UserProfile>('/auth/mfa/disable', {
      method: 'POST',
      body: JSON.stringify({ code }),
    }),
  register: (payload: {
    email: string;
    full_name: string;
    password: string;
    role: UserRole;
  }) =>
    apiRequest<TokenResponse>('/auth/register', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  refreshToken: (refreshToken: string) =>
    apiRequest<TokenResponse>('/auth/refresh', {
      method: 'POST',
      body: JSON.stringify({ refresh_token: refreshToken }),
    }),
  logout: (refreshToken?: string | null) =>
    apiRequest<{ detail: string }>('/auth/logout', {
      method: 'POST',
      body: JSON.stringify({ refresh_token: refreshToken ?? null }),
    }),
  me: () => apiRequest<UserProfile>('/auth/me'),
  listUsers: () => apiRequest<UserProfile[]>('/auth/users'),

  // Matters & Memberships
  listMatters: () => apiRequest<Matter[]>('/matters'),
  createMatter: (payload: MatterCreateInput) =>
    apiRequest<Matter>('/matters', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  deleteMatter: (matterId: string) =>
    apiRequest<void>(`/matters/${matterId}`, { method: 'DELETE' }),
  listMatterMembers: (matterId: string) =>
    apiRequest<MatterMember[]>(`/matters/${matterId}/members`),
  addMatterMember: (matterId: string, userId: string, accessLevel: AccessLevel) =>
    apiRequest<MatterMember>(`/matters/${matterId}/members`, {
      method: 'POST',
      body: JSON.stringify({ user_id: userId, access_level: accessLevel }),
    }),
  removeMatterMember: (matterId: string, userId: string) =>
    apiRequest<void>(`/matters/${matterId}/members/${userId}`, {
      method: 'DELETE',
    }),
  toggleLegalHold: (matterId: string, active: boolean, reason?: string) =>
    apiRequest<Matter>(`/matters/${matterId}/legal-hold`, {
      method: 'POST',
      body: JSON.stringify({ active, reason }),
    }),

  // Playbooks
  listPlaybooks: () => apiRequest<Playbook[]>('/playbooks'),
  createPlaybook: (payload: {
    name: string;
    practice_area: string;
    description?: string;
    is_default?: boolean;
    rules: PlaybookRule[];
  }) =>
    apiRequest<Playbook>('/playbooks', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  deletePlaybook: (playbookId: string) =>
    apiRequest<void>(`/playbooks/${playbookId}`, { method: 'DELETE' }),

  // Documents, Privilege & Bates
  listDocuments: (matterId: string) =>
    apiRequest<DocumentItem[]>(`/matters/${matterId}/documents`),
  getDocument: (documentId: string) => apiRequest<DocumentItem>(`/documents/${documentId}`),
  uploadDocument: (matterId: string, file: File, documentType = 'general') => {
    const form = new FormData();
    form.append('file', file);
    form.append('document_type', documentType);
    return apiRequest<DocumentUploadResponse>(`/matters/${matterId}/documents`, {
      method: 'POST',
      body: form,
    });
  },
  getDocumentChunks: (documentId: string) =>
    apiRequest<DocumentChunkItem[]>(`/documents/${documentId}/chunks`),
  documentContentUrl: (documentId: string, download = false) =>
    `${API_BASE}/documents/${documentId}/content?download=${download ? 'true' : 'false'}${authQuerySuffix('&')}`,
  updateDocumentPrivilege: (
    documentId: string,
    payload: { privilege_status: PrivilegeStatus; privilege_reason?: string }
  ) =>
    apiRequest<DocumentItem>(`/documents/${documentId}/privilege`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  batesStampDocuments: (
    matterId: string,
    payload: { prefix: string; start_number: number; zero_pad?: number; document_ids?: string[] }
  ) =>
    apiRequest<BatesStampResponse>(`/matters/${matterId}/documents/bates-stamp`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  reprocessDocument: (documentId: string) =>
    apiRequest<DocumentUploadResponse>(`/documents/${documentId}/reprocess`, {
      method: 'POST',
    }),
  deleteDocument: (documentId: string) =>
    apiRequest<void>(`/documents/${documentId}`, { method: 'DELETE' }),

  // Analyses, Matrix & Drafting
  runContractReview: (
    matterId: string,
    payload: {
      document_ids?: string[];
      playbook_notes?: string;
      playbook_id?: string;
      provider?: LLMProviderName;
      model?: string;
    }
  ) =>
    apiRequest<ReviewTaskItem>(`/matters/${matterId}/analyses/contract-review`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  runBatchContractReview: (
    matterId: string,
    payload: {
      document_ids?: string[];
      playbook_notes?: string;
      playbook_id?: string;
      provider?: LLMProviderName;
      model?: string;
    }
  ) =>
    apiRequest<ReviewTaskItem>(`/matters/${matterId}/analyses/batch-contract-review`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  getRiskMatrix: (matterId: string) =>
    apiRequest<PortfolioRiskMatrix>(`/matters/${matterId}/analyses/risk-matrix`),

  runDiscoverySynthesis: (
    matterId: string,
    payload: {
      document_ids?: string[];
      focus_topics?: string[];
      provider?: LLMProviderName;
      model?: string;
    }
  ) =>
    apiRequest<ReviewTaskItem>(`/matters/${matterId}/analyses/discovery-synthesis`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  searchMatter: (matterId: string, query: string, topK = 8) =>
    apiRequest<SearchResponse>(`/matters/${matterId}/search`, {
      method: 'POST',
      body: JSON.stringify({ query, top_k: topK }),
    }),

  researchQuery: (
    matterId: string,
    payload: {
      question: string;
      top_k?: number;
      provider?: LLMProviderName;
      model?: string;
      save_to_queue?: boolean;
    }
  ) =>
    apiRequest<ResearchQueryResponse>(`/matters/${matterId}/research/query`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  generateDraft: (
    matterId: string,
    payload: {
      draft_type:
        | 'internal_memo'
        | 'clause_comparison'
        | 'discovery_requests'
        | 'discovery_responses'
        | 'review_checklist';
      instructions: string;
      provider?: LLMProviderName;
      model?: string;
    }
  ) =>
    apiRequest<ReviewTaskItem>(`/matters/${matterId}/drafts`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  listTasks: (matterId: string) =>
    apiRequest<ReviewTaskItem[]>(`/matters/${matterId}/tasks`),
  getTask: (taskId: string) => apiRequest<ReviewTaskItem>(`/tasks/${taskId}`),

  // Findings & Review Queue
  listFindings: (matterId: string, params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params).toString()}` : '';
    return apiRequest<ExtractedFinding[]>(`/matters/${matterId}/findings${qs}`);
  },
  reviewFinding: (
    findingId: string,
    payload: {
      review_status: ReviewStatus;
      reviewer_notes?: string;
      edited_summary?: string;
    }
  ) =>
    apiRequest<ExtractedFinding>(`/findings/${findingId}/review`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  batchReviewFindings: (payload: {
    finding_ids: string[];
    review_status: ReviewStatus;
    reviewer_notes?: string;
  }) =>
    apiRequest<{ updated_count: number; findings: ExtractedFinding[] }>(
      '/findings/batch-review',
      {
        method: 'POST',
        body: JSON.stringify(payload),
      }
    ),

  listAuditLogs: (matterId: string) =>
    apiRequest<AuditLogEntry[]>(`/matters/${matterId}/audit-logs`),
  verifyAuditChain: (matterId: string) =>
    apiRequest<AuditChainVerification>(`/matters/${matterId}/audit-logs/verify`),

  // DMS Integrations, Webhooks & Enterprise Admin
  listDMSProviders: () => apiRequest<DMSProviderInfo[]>('/integrations/dms/providers'),
  listDMSFiles: (provider: string) =>
    apiRequest<DMSFileItem[]>(`/integrations/dms/${encodeURIComponent(provider)}/files`),
  importDMSFile: (matterId: string, provider: string, externalId: string, documentType = 'general') =>
    apiRequest<DocumentUploadResponse>(`/matters/${matterId}/integrations/dms/import`, {
      method: 'POST',
      body: JSON.stringify({ provider, external_id: externalId, document_type: documentType }),
    }),
  listWebhooks: () => apiRequest<WebhookSubscription[]>('/integrations/webhooks'),
  createWebhook: (payload: WebhookCreateInput) =>
    apiRequest<WebhookSubscription>('/integrations/webhooks', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateWebhook: (
    webhookId: string,
    payload: { name?: string; is_active?: boolean; events?: string[] }
  ) =>
    apiRequest<WebhookSubscription>(`/integrations/webhooks/${webhookId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  deleteWebhook: (webhookId: string) =>
    apiRequest<void>(`/integrations/webhooks/${webhookId}`, { method: 'DELETE' }),
  testWebhook: (webhookId: string) =>
    apiRequest<WebhookTestResult>(`/integrations/webhooks/${webhookId}/test`, {
      method: 'POST',
    }),
  getOrganization: () => apiRequest<OrganizationInfo>('/admin/organization'),
  updateOrganization: (payload: { name?: string; retention_days?: number }) =>
    apiRequest<OrganizationInfo>('/admin/organization', {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  updateAdminUser: (userId: string, payload: { role?: UserRole; is_active?: boolean }) =>
    apiRequest<UserProfile>(`/admin/users/${userId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  runRetentionSweep: (payload: { dry_run: boolean; as_of?: string }) =>
    apiRequest<RetentionSweepReport>('/admin/retention/sweep', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateDocumentRetention: (documentId: string, retention_until: string | null) =>
    apiRequest<DocumentItem>(`/documents/${documentId}/retention`, {
      method: 'PATCH',
      body: JSON.stringify({ retention_until }),
    }),

  // Legal-Ops: Version Diff, Conflict Screening, Ethical Walls, Obligations & Streaming
  compareDocuments: (matterId: string, baseDocumentId: string, revisedDocumentId: string) =>
    apiRequest<DocumentComparisonResponse>(`/matters/${matterId}/documents/compare`, {
      method: 'POST',
      body: JSON.stringify({
        base_document_id: baseDocumentId,
        revised_document_id: revisedDocumentId,
      }),
    }),
  runConflictCheck: (payload: {
    client_name: string;
    counterparties?: string[];
    matter_title?: string;
  }) =>
    apiRequest<ConflictCheckResponse>('/matters/conflict-check', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  listEthicalWalls: (matterId: string) =>
    apiRequest<EthicalWallEntry[]>(`/matters/${matterId}/ethical-walls`),
  createEthicalWall: (matterId: string, payload: { user_id: string; reason: string }) =>
    apiRequest<EthicalWallEntry>(`/matters/${matterId}/ethical-walls`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  deleteEthicalWall: (matterId: string, userId: string) =>
    apiRequest<void>(`/matters/${matterId}/ethical-walls/${userId}`, {
      method: 'DELETE',
    }),
  listObligations: (matterId: string) =>
    apiRequest<MatterObligation[]>(`/matters/${matterId}/obligations`),
  createObligation: (
    matterId: string,
    payload: {
      title: string;
      category?: string;
      due_date: string;
      notice_window_days?: number;
      owner_name?: string;
      notes?: string;
    }
  ) =>
    apiRequest<MatterObligation>(`/matters/${matterId}/obligations`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  extractObligations: (matterId: string) =>
    apiRequest<MatterObligation[]>(`/matters/${matterId}/obligations/extract`, {
      method: 'POST',
    }),
  updateObligation: (
    obligationId: string,
    payload: { status?: ObligationStatus; notes?: string }
  ) =>
    apiRequest<MatterObligation>(`/obligations/${obligationId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  deleteObligation: (obligationId: string) =>
    apiRequest<void>(`/obligations/${obligationId}`, { method: 'DELETE' }),
  obligationsCalendarUrl: (matterId: string) =>
    `${API_BASE}/matters/${matterId}/obligations/calendar.ics${authQuerySuffix('?')}`,
  checkObligationAlerts: (matterId: string, dueWithinDays = 14) =>
    apiRequest<ObligationAlertResult>(
      `/matters/${matterId}/obligations/check-alerts?due_within_days=${dueWithinDays}`,
      { method: 'POST' }
    ),
  researchQueryStream: async (
    matterId: string,
    payload: {
      question: string;
      top_k?: number;
      provider?: LLMProviderName;
      model?: string;
      save_to_queue?: boolean;
    },
    callbacks: {
      onToken?: (delta: string) => void;
      onRetrieval?: (info: { hit_count: number }) => void;
    } = {}
  ): Promise<ResearchQueryResponse> => {
    const token = tokenStore.get();
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (token) headers.Authorization = `Bearer ${token}`;

    const resp = await fetch(`${API_BASE}/matters/${matterId}/research/query/stream`, {
      method: 'POST',
      headers,
      body: JSON.stringify(payload),
    });
    if (!resp.ok || !resp.body) {
      throw new Error(`Streaming research error (${resp.status})`);
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let finalResult: ResearchQueryResponse | null = null;

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split(/\r?\n\r?\n/);
      buffer = frames.pop() ?? '';
      for (const frame of frames) {
        let eventName = 'message';
        let dataLine = '';
        for (const rawLine of frame.split(/\r?\n/)) {
          if (rawLine.startsWith('event:')) {
            eventName = rawLine.slice(6).trim();
          } else if (rawLine.startsWith('data:')) {
            dataLine += rawLine.slice(5).trim();
          }
        }
        if (!dataLine) continue;
        const parsed = JSON.parse(dataLine) as Record<string, unknown>;
        if (eventName === 'retrieval' && callbacks.onRetrieval) {
          callbacks.onRetrieval(parsed as { hit_count: number });
        } else if (eventName === 'token' && callbacks.onToken) {
          callbacks.onToken(String(parsed.delta ?? ''));
        } else if (eventName === 'complete') {
          finalResult = parsed as unknown as ResearchQueryResponse;
        }
      }
    }
    if (!finalResult) {
      throw new Error('Streaming research ended without completion payload.');
    }
    return finalResult;
  },

  matterEventsUrl: (matterId: string) =>
    `${API_BASE}/matters/${matterId}/events${authQuerySuffix('?')}`,

  exportReviewTableUrl: (matterId: string, format: 'csv' | 'json' | 'markdown') =>
    `${API_BASE}/matters/${matterId}/exports/review-table?format=${format}${authQuerySuffix('&')}`,
  exportChronologyUrl: (matterId: string, format: 'csv' | 'json' | 'markdown') =>
    `${API_BASE}/matters/${matterId}/exports/chronology?format=${format}${authQuerySuffix('&')}`,
  exportRedlineDocxUrl: (matterId: string) =>
    `${API_BASE}/matters/${matterId}/exports/redline.docx${authQuerySuffix('?')}`,
  exportPrivilegeLogUrl: (matterId: string, format: 'csv' | 'json' | 'markdown' | 'docx') =>
    `${API_BASE}/matters/${matterId}/exports/privilege-log?format=${format}${authQuerySuffix('&')}`,
  exportRiskMatrixUrl: (matterId: string, format: 'csv' | 'json') =>
    `${API_BASE}/matters/${matterId}/exports/risk-matrix?format=${format}${authQuerySuffix('&')}`,
  exportEvalDatasetUrl: (matterId: string) =>
    `${API_BASE}/matters/${matterId}/exports/eval-dataset${authQuerySuffix('?')}`,
};
