import { useState } from 'react';
import {
  AlertOctagon,
  BookOpen,
  Check,
  Download,
  Flag,
  Loader2,
  Play,
  Plus,
  ShieldAlert,
} from 'lucide-react';
import { api } from '../api/client';
import type {
  DocumentItem,
  ExtractedFinding,
  HealthResponse,
  LLMProviderName,
  Playbook,
  ReviewStatus,
  ReviewTaskItem,
  UserProfile,
} from '../types/api';
import { CitationBadge } from './CitationBadge';

interface ContractReviewTabProps {
  matterId: string;
  documents: DocumentItem[];
  findings: ExtractedFinding[];
  playbooks: Playbook[];
  currentUser: UserProfile;
  health: HealthResponse | null;
  onNewPlaybook: () => void;
  onRefresh: () => Promise<void>;
}

const SEVERITY_BADGE: Record<string, string> = {
  critical: 'bg-rose-100 text-rose-900 border-rose-300',
  high: 'bg-orange-100 text-orange-900 border-orange-300',
  medium: 'bg-amber-100 text-amber-900 border-amber-300',
  low: 'bg-sky-100 text-sky-900 border-sky-300',
  info: 'bg-slate-100 text-slate-700 border-slate-200',
};

export function ContractReviewTab({
  matterId,
  documents,
  findings,
  playbooks,
  currentUser,
  health,
  onNewPlaybook,
  onRefresh,
}: ContractReviewTabProps) {
  const [selectedDocIds, setSelectedDocIds] = useState<string[]>([]);
  const [selectedPlaybookId, setSelectedPlaybookId] = useState<string>('');
  const [provider, setProvider] = useState<LLMProviderName | ''>('');
  const [model, setModel] = useState('');
  const [playbookNotes, setPlaybookNotes] = useState('');
  const [running, setRunning] = useState(false);
  const [lastTask, setLastTask] = useState<ReviewTaskItem | null>(null);
  const [categoryFilter, setCategoryFilter] = useState('all');
  const [error, setError] = useState<string | null>(null);

  const canWrite = currentUser.role !== 'auditor';
  const canApprove = currentUser.role === 'partner' || currentUser.role === 'associate';

  const indexedDocs = documents.filter((d) => d.ingestion_status === 'indexed');
  const contractFindings = findings.filter((f) =>
    ['clause', 'risk_flag', 'missing_protection'].includes(f.finding_type)
  );

  const filteredFindings =
    categoryFilter === 'all'
      ? contractFindings
      : contractFindings.filter((f) => f.finding_type === categoryFilter || f.severity === categoryFilter);

  const availableModels = provider && health ? health.supported_models[provider] ?? [] : [];

  function toggleDoc(id: string) {
    setSelectedDocIds((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]
    );
  }

  async function handleRunReview() {
    setError(null);
    setRunning(true);
    try {
      const task = await api.runContractReview(matterId, {
        document_ids: selectedDocIds.length > 0 ? selectedDocIds : undefined,
        playbook_notes: playbookNotes.trim() || undefined,
        playbook_id: selectedPlaybookId || undefined,
        provider: provider || undefined,
        model: model || undefined,
      });
      setLastTask(task);
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Contract review failed.');
    } finally {
      setRunning(false);
    }
  }

  async function quickReview(findingId: string, status: ReviewStatus) {
    try {
      await api.reviewFinding(findingId, {
        review_status: status,
      });
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to update review state.');
    }
  }

  const criticalOrHigh = contractFindings.filter(
    (f) => f.severity === 'critical' || f.severity === 'high'
  ).length;
  const missingCount = contractFindings.filter(
    (f) => f.finding_type === 'missing_protection'
  ).length;
  const escalatedCount = contractFindings.filter((f) => f.requires_escalation).length;
  const avgGrounding = contractFindings.length
    ? contractFindings.reduce((acc, f) => acc + f.grounding_score, 0) / contractFindings.length
    : 0;

  return (
    <div className="space-y-6">
      {error && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-800">
          {error}
        </div>
      )}

      {/* Control Panel */}
      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex flex-col justify-between gap-4 lg:flex-row lg:items-end">
          <div className="space-y-2 flex-1">
            <h3 className="text-sm font-semibold text-slate-900">
              Contract &amp; Clause Risk Extractor
            </h3>
            <p className="text-xs text-slate-500">
              Analyzes indemnification, limitation of liability, termination, IP assignment,
              confidentiality, governing law, renewal windows, and missing protections.
            </p>

            {indexedDocs.length > 0 && (
              <div className="flex flex-wrap gap-1.5 pt-1">
                {indexedDocs.map((doc) => {
                  const checked = selectedDocIds.includes(doc.id);
                  return (
                    <button
                      key={doc.id}
                      type="button"
                      onClick={() => toggleDoc(doc.id)}
                      className={`rounded-full border px-2.5 py-1 text-[11px] font-medium transition ${
                        checked
                          ? 'border-indigo-500 bg-indigo-50 text-indigo-800'
                          : 'border-slate-200 bg-slate-50 text-slate-600 hover:bg-slate-100'
                      }`}
                    >
                      {checked ? '✓ ' : ''}
                      {doc.filename}
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2.5">
            <select
              value={provider}
              onChange={(e) => {
                setProvider(e.target.value as LLMProviderName | '');
                setModel('');
              }}
              aria-label="LLM provider"
              className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs text-slate-700"
            >
              <option value="">Default Router ({health?.default_provider ?? 'mock'})</option>
              <option value="mock">Mock (Offline Deterministic)</option>
              <option value="ollama">Ollama (Local Open-Weight)</option>
              <option value="gemini">Gemini (Cloud)</option>
            </select>

            {availableModels.length > 0 && (
              <select
                value={model}
                onChange={(e) => setModel(e.target.value)}
                aria-label="Model name"
                className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs text-slate-700"
              >
                <option value="">Provider Default Model</option>
                {availableModels.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            )}

            <button
              type="button"
              disabled={running || indexedDocs.length === 0 || !canWrite}
              onClick={() => void handleRunReview()}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:opacity-50"
            >
              {running ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Play className="h-4 w-4" />
              )}
              {running ? 'Analyzing Contract...' : 'Run Contract Review'}
            </button>

            <a
              href={api.exportRedlineDocxUrl(matterId)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50"
            >
              <Download className="h-4 w-4" />
              Word Redline (.docx)
            </a>
          </div>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2.5">
          <div className="flex items-center gap-1.5">
            <BookOpen className="h-3.5 w-3.5 text-slate-500" />
            <select
              value={selectedPlaybookId}
              onChange={(e) => setSelectedPlaybookId(e.target.value)}
              aria-label="Contract Playbook"
              className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-700"
            >
              <option value="">Default Firm Playbook</option>
              {playbooks.map((pb) => (
                <option key={pb.id} value={pb.id}>
                  {pb.name} ({pb.rules_json.length} rules)
                </option>
              ))}
            </select>
            {canApprove && (
              <button
                type="button"
                onClick={onNewPlaybook}
                className="inline-flex items-center gap-1 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
              >
                <Plus className="h-3.5 w-3.5" />
                Playbook
              </button>
            )}
          </div>
          <input
            type="text"
            value={playbookNotes}
            onChange={(e) => setPlaybookNotes(e.target.value)}
            placeholder="Optional playbook instructions (e.g., Flag liability caps under 12 months fees or any client background IP assignment)"
            className="flex-1 min-w-[240px] rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs text-slate-800 placeholder:text-slate-400 focus:border-indigo-500 focus:bg-white focus:outline-none"
          />
        </div>

        {lastTask?.result_json && typeof lastTask.result_json.executive_summary === 'string' && (
          <div className="mt-3 rounded-lg border border-indigo-200 bg-indigo-50/70 px-3.5 py-2.5 text-xs text-indigo-950">
            <strong>Latest Run ({lastTask.resolved_provider}/{lastTask.resolved_model}):</strong>{' '}
            {lastTask.result_json.executive_summary}
          </div>
        )}
      </div>

      {/* KPI Strip */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <p className="text-[11px] font-medium uppercase tracking-wide text-slate-500">
            Total Contract Findings
          </p>
          <p className="mt-1 text-2xl font-bold text-slate-900">{contractFindings.length}</p>
        </div>
        <div className="rounded-xl border border-rose-200 bg-rose-50/50 p-4">
          <p className="text-[11px] font-medium uppercase tracking-wide text-rose-700">
            Critical / High Risk
          </p>
          <p className="mt-1 text-2xl font-bold text-rose-900">{criticalOrHigh}</p>
        </div>
        <div className="rounded-xl border border-amber-200 bg-amber-50/50 p-4">
          <p className="text-[11px] font-medium uppercase tracking-wide text-amber-800">
            Missing Protections
          </p>
          <p className="mt-1 text-2xl font-bold text-amber-900">{missingCount}</p>
        </div>
        <div className="rounded-xl border border-emerald-200 bg-emerald-50/50 p-4">
          <p className="text-[11px] font-medium uppercase tracking-wide text-emerald-800">
            Avg Citation Grounding
          </p>
          <p className="mt-1 text-2xl font-bold text-emerald-900">
            {(avgGrounding * 100).toFixed(0)}%
            <span className="ml-2 text-xs font-normal text-slate-500">
              ({escalatedCount} escalated)
            </span>
          </p>
        </div>
      </div>

      {/* Filter pills */}
      <div className="flex flex-wrap items-center gap-2">
        {[
          { key: 'all', label: 'All Findings' },
          { key: 'risk_flag', label: 'Risk Flags' },
          { key: 'missing_protection', label: 'Missing Protections' },
          { key: 'clause', label: 'Standard Clauses' },
          { key: 'critical', label: 'Critical Severity' },
        ].map((f) => (
          <button
            key={f.key}
            type="button"
            onClick={() => setCategoryFilter(f.key)}
            className={`rounded-full px-3 py-1 text-xs font-medium transition ${
              categoryFilter === f.key
                ? 'bg-slate-900 text-white'
                : 'border border-slate-200 bg-white text-slate-600 hover:bg-slate-50'
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* Findings List */}
      <div className="space-y-3">
        {filteredFindings.length === 0 ? (
          <div className="rounded-xl border border-slate-200 bg-white p-10 text-center text-xs text-slate-500">
            No contract findings yet. Upload and index a contract, then click{' '}
            <strong>Run Contract Review</strong>.
          </div>
        ) : (
          filteredFindings.map((finding) => (
            <div
              key={finding.id}
              className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm space-y-3"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span
                      className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase ${
                        SEVERITY_BADGE[finding.severity] || SEVERITY_BADGE.info
                      }`}
                    >
                      {finding.severity}
                    </span>
                    <span className="rounded bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-700">
                      {finding.category.replace(/_/g, ' ')}
                    </span>
                    <span className="rounded bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600">
                      {finding.finding_type.replace(/_/g, ' ')}
                    </span>
                    {finding.requires_escalation && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-rose-50 px-2 py-0.5 text-[10px] font-semibold text-rose-700 ring-1 ring-rose-200">
                        <AlertOctagon className="h-3 w-3" />
                        Escalated
                      </span>
                    )}
                  </div>
                  <h4 className="text-sm font-semibold text-slate-900">{finding.title}</h4>
                </div>

                <div className="flex items-center gap-1.5">
                  <span className="mr-2 text-[11px] text-slate-500">
                    Conf {(finding.confidence * 100).toFixed(0)}% • Ground{' '}
                    {(finding.grounding_score * 100).toFixed(0)}%
                  </span>
                  <button
                    type="button"
                    disabled={!canApprove}
                    title={canApprove ? 'Approve finding' : 'Licensed attorney role required to approve'}
                    onClick={() => void quickReview(finding.id, 'approved')}
                    className={`inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium border disabled:opacity-40 ${
                      finding.review_status === 'approved'
                        ? 'border-emerald-500 bg-emerald-600 text-white'
                        : 'border-slate-200 bg-white text-slate-700 hover:bg-emerald-50'
                    }`}
                  >
                    <Check className="h-3.5 w-3.5" />
                    Approve
                  </button>
                  <button
                    type="button"
                    disabled={!canWrite}
                    onClick={() => void quickReview(finding.id, 'flagged')}
                    className={`inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium border disabled:opacity-40 ${
                      finding.review_status === 'flagged'
                        ? 'border-rose-500 bg-rose-600 text-white'
                        : 'border-slate-200 bg-white text-slate-700 hover:bg-rose-50'
                    }`}
                  >
                    <Flag className="h-3.5 w-3.5" />
                    Flag
                  </button>
                </div>
              </div>

              <p className="text-xs leading-relaxed text-slate-700">
                {finding.edited_summary || finding.summary}
              </p>

              {typeof finding.metadata_json?.proposed_redline === 'string' && (
                <div className="grid gap-3 rounded-lg border border-slate-200 bg-slate-50/70 p-3 md:grid-cols-2">
                  <div>
                    <p className="text-[10px] font-bold uppercase tracking-wider text-rose-700">
                      Original Contract Language (Cited)
                    </p>
                    <p className="mt-1 rounded border border-rose-200 bg-rose-50/60 p-2 font-mono text-[11px] leading-relaxed text-rose-950 line-through">
                      {finding.citations_json[0]?.quote || '[Missing protective clause]'}
                    </p>
                  </div>
                  <div>
                    <p className="text-[10px] font-bold uppercase tracking-wider text-emerald-700">
                      Proposed Playbook Fallback Redline
                    </p>
                    <p className="mt-1 rounded border border-emerald-200 bg-emerald-50/60 p-2 font-mono text-[11px] leading-relaxed text-emerald-950">
                      {finding.metadata_json.proposed_redline}
                    </p>
                  </div>
                </div>
              )}

              {finding.recommended_action && (
                <div className="flex items-start gap-2 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-700">
                  <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-indigo-600" />
                  <span>
                    <strong>Recommended Attorney Action:</strong> {finding.recommended_action}
                  </span>
                </div>
              )}

              {finding.citations_json.length > 0 && (
                <div className="space-y-1.5 pt-1">
                  {finding.citations_json.map((cit, idx) => (
                    <CitationBadge key={`${finding.id}-cit-${idx}`} citation={cit} index={idx} />
                  ))}
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
