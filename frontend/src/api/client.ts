import type {
  AuditLogEntry,
  DocumentChunkItem,
  DocumentItem,
  DocumentUploadResponse,
  ExtractedFinding,
  HealthResponse,
  LLMProviderName,
  Matter,
  MatterCreateInput,
  ResearchQueryResponse,
  ReviewStatus,
  ReviewTaskItem,
  SearchResponse,
} from '../types/api';

const API_BASE = '/api/v1';

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!response.ok) {
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

export const api = {
  health: () => apiRequest<HealthResponse>('/health'),

  listMatters: () => apiRequest<Matter[]>('/matters'),
  createMatter: (payload: MatterCreateInput) =>
    apiRequest<Matter>('/matters', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  deleteMatter: (matterId: string) =>
    apiRequest<void>(`/matters/${matterId}`, { method: 'DELETE' }),

  listDocuments: (matterId: string) =>
    apiRequest<DocumentItem[]>(`/matters/${matterId}/documents`),
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
  reprocessDocument: (documentId: string) =>
    apiRequest<DocumentUploadResponse>(`/documents/${documentId}/reprocess`, {
      method: 'POST',
    }),
  deleteDocument: (documentId: string) =>
    apiRequest<void>(`/documents/${documentId}`, { method: 'DELETE' }),

  runContractReview: (
    matterId: string,
    payload: {
      document_ids?: string[];
      playbook_notes?: string;
      provider?: LLMProviderName;
      model?: string;
    }
  ) =>
    apiRequest<ReviewTaskItem>(`/matters/${matterId}/analyses/contract-review`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

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

  listFindings: (matterId: string, params?: Record<string, string>) => {
    const qs = params ? `?${new URLSearchParams(params).toString()}` : '';
    return apiRequest<ExtractedFinding[]>(`/matters/${matterId}/findings${qs}`);
  },
  reviewFinding: (
    findingId: string,
    payload: {
      review_status: ReviewStatus;
      reviewer_name: string;
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
    reviewer_name: string;
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

  exportReviewTableUrl: (matterId: string, format: 'csv' | 'json' | 'markdown') =>
    `${API_BASE}/matters/${matterId}/exports/review-table?format=${format}`,
  exportChronologyUrl: (matterId: string, format: 'csv' | 'json' | 'markdown') =>
    `${API_BASE}/matters/${matterId}/exports/chronology?format=${format}`,
};
