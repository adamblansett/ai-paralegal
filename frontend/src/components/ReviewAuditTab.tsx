import { useState } from 'react';
import {
  Check,
  ChevronDown,
  ChevronRight,
  Edit3,
  Flag,
  History,
  RotateCcw,
  ShieldCheck,
} from 'lucide-react';
import { api } from '../api/client';
import type { AuditLogEntry, ExtractedFinding, ReviewStatus } from '../types/api';
import { CitationBadge } from './CitationBadge';

interface ReviewAuditTabProps {
  findings: ExtractedFinding[];
  auditLogs: AuditLogEntry[];
  onRefresh: () => Promise<void>;
}

const REVIEW_BADGE: Record<ReviewStatus, string> = {
  pending_review: 'bg-amber-50 text-amber-800 border-amber-200',
  approved: 'bg-emerald-50 text-emerald-800 border-emerald-200',
  flagged: 'bg-rose-50 text-rose-800 border-rose-200',
};

export function ReviewAuditTab({
  findings,
  auditLogs,
  onRefresh,
}: ReviewAuditTabProps) {
  const [view, setView] = useState<'queue' | 'audit'>('queue');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [reviewerName, setReviewerName] = useState('A. Vance, Esq.');
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editSummary, setEditSummary] = useState('');
  const [editNotes, setEditNotes] = useState('');
  const [expandedAuditId, setExpandedAuditId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const filteredFindings = findings.filter((f) => {
    if (statusFilter === 'all') return true;
    if (statusFilter === 'escalated') return f.requires_escalation;
    return f.review_status === statusFilter;
  });

  function toggleSelect(id: string) {
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]
    );
  }

  async function handleReview(
    finding: ExtractedFinding,
    status: ReviewStatus,
    withEdits = false
  ) {
    setError(null);
    try {
      await api.reviewFinding(finding.id, {
        review_status: status,
        reviewer_name: reviewerName.trim() || 'Reviewing Attorney',
        reviewer_notes: withEdits ? editNotes : finding.reviewer_notes ?? undefined,
        edited_summary: withEdits && editSummary.trim() ? editSummary : undefined,
      });
      setEditingId(null);
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update finding.');
    }
  }

  async function handleBatch(status: ReviewStatus) {
    if (selectedIds.length === 0) return;
    setError(null);
    try {
      await api.batchReviewFindings({
        finding_ids: selectedIds,
        review_status: status,
        reviewer_name: reviewerName.trim() || 'Reviewing Attorney',
        reviewer_notes: `Batch ${status} via Attorney Review Queue`,
      });
      setSelectedIds([]);
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Batch review failed.');
    }
  }

  return (
    <div className="space-y-5">
      {error && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-800">
          {error}
        </div>
      )}

      {/* Sub-view Switcher & Reviewer Identity */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setView('queue')}
            className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
              view === 'queue'
                ? 'bg-indigo-600 text-white'
                : 'text-slate-600 hover:bg-slate-100'
            }`}
          >
            <ShieldCheck className="h-3.5 w-3.5" />
            Attorney Review Queue ({findings.length})
          </button>
          <button
            type="button"
            onClick={() => setView('audit')}
            className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
              view === 'audit'
                ? 'bg-indigo-600 text-white'
                : 'text-slate-600 hover:bg-slate-100'
            }`}
          >
            <History className="h-3.5 w-3.5" />
            Immutable Audit Trail ({auditLogs.length})
          </button>
        </div>

        <div className="flex items-center gap-2 text-xs">
          <span className="text-slate-500">Reviewer:</span>
          <input
            type="text"
            value={reviewerName}
            onChange={(e) => setReviewerName(e.target.value)}
            className="rounded-lg border border-slate-300 px-2.5 py-1 text-xs font-medium text-slate-800 focus:border-indigo-500 focus:outline-none"
          />
        </div>
      </div>

      {/* ATTORNEY REVIEW QUEUE */}
      {view === 'queue' && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap gap-1.5">
              {[
                { key: 'all', label: 'All' },
                { key: 'pending_review', label: 'Pending Review' },
                { key: 'flagged', label: 'Flagged' },
                { key: 'approved', label: 'Approved' },
                { key: 'escalated', label: 'Escalated Only' },
              ].map((item) => (
                <button
                  key={item.key}
                  type="button"
                  onClick={() => setStatusFilter(item.key)}
                  className={`rounded-full px-3 py-1 text-xs font-medium transition ${
                    statusFilter === item.key
                      ? 'bg-slate-900 text-white'
                      : 'border border-slate-200 bg-white text-slate-600 hover:bg-slate-50'
                  }`}
                >
                  {item.label}
                </button>
              ))}
            </div>

            {selectedIds.length > 0 && (
              <div className="flex items-center gap-2">
                <span className="text-xs text-slate-600">
                  {selectedIds.length} selected
                </span>
                <button
                  type="button"
                  onClick={() => void handleBatch('approved')}
                  className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-500"
                >
                  Batch Approve
                </button>
                <button
                  type="button"
                  onClick={() => void handleBatch('flagged')}
                  className="rounded-lg bg-rose-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-rose-500"
                >
                  Batch Flag
                </button>
              </div>
            )}
          </div>

          {filteredFindings.length === 0 ? (
            <div className="rounded-xl border border-slate-200 bg-white p-10 text-center text-xs text-slate-500">
              No findings match this review filter.
            </div>
          ) : (
            <div className="space-y-3">
              {filteredFindings.map((finding) => {
                const isEditing = editingId === finding.id;
                const isSelected = selectedIds.includes(finding.id);

                return (
                  <div
                    key={finding.id}
                    className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm space-y-3"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="flex items-start gap-2.5">
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => toggleSelect(finding.id)}
                          aria-label={`Select ${finding.title}`}
                          className="mt-1 rounded border-slate-300 text-indigo-600"
                        />
                        <div className="space-y-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <span
                              className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase ${
                                REVIEW_BADGE[finding.review_status]
                              }`}
                            >
                              {finding.review_status.replace('_', ' ')}
                            </span>
                            <span className="rounded bg-slate-100 px-2 py-0.5 text-[10px] font-semibold uppercase text-slate-700">
                              {finding.finding_type.replace(/_/g, ' ')}
                            </span>
                            <span className="text-[11px] text-slate-500">
                              Severity: <strong>{finding.severity}</strong> • Grounding{' '}
                              {(finding.grounding_score * 100).toFixed(0)}%
                            </span>
                          </div>
                          <h4 className="text-sm font-semibold text-slate-900">{finding.title}</h4>
                        </div>
                      </div>

                      <div className="flex flex-wrap items-center gap-1.5">
                        <button
                          type="button"
                          onClick={() => {
                            if (isEditing) {
                              setEditingId(null);
                            } else {
                              setEditingId(finding.id);
                              setEditSummary(finding.edited_summary || finding.summary);
                              setEditNotes(finding.reviewer_notes || '');
                            }
                          }}
                          className="inline-flex items-center gap-1 rounded border border-slate-200 px-2.5 py-1 text-xs text-slate-700 hover:bg-slate-50"
                        >
                          <Edit3 className="h-3 w-3" />
                          Edit
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleReview(finding, 'approved')}
                          className="inline-flex items-center gap-1 rounded border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-800 hover:bg-emerald-100"
                        >
                          <Check className="h-3.5 w-3.5" />
                          Approve
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleReview(finding, 'flagged')}
                          className="inline-flex items-center gap-1 rounded border border-rose-200 bg-rose-50 px-2.5 py-1 text-xs font-medium text-rose-800 hover:bg-rose-100"
                        >
                          <Flag className="h-3.5 w-3.5" />
                          Flag
                        </button>
                        {finding.review_status !== 'pending_review' && (
                          <button
                            type="button"
                            onClick={() => void handleReview(finding, 'pending_review')}
                            title="Reset to pending_review"
                            className="rounded border border-slate-200 p-1 text-slate-500 hover:bg-slate-50"
                          >
                            <RotateCcw className="h-3.5 w-3.5" />
                          </button>
                        )}
                      </div>
                    </div>

                    {isEditing ? (
                      <div className="space-y-2.5 rounded-lg border border-indigo-200 bg-indigo-50/40 p-3.5">
                        <div>
                          <label className="block text-[11px] font-semibold text-slate-700">
                            Attorney-Edited Finding / Draft Summary
                          </label>
                          <textarea
                            rows={4}
                            value={editSummary}
                            onChange={(e) => setEditSummary(e.target.value)}
                            className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs"
                          />
                        </div>
                        <div>
                          <label className="block text-[11px] font-semibold text-slate-700">
                            Reviewer Notes
                          </label>
                          <input
                            type="text"
                            value={editNotes}
                            onChange={(e) => setEditNotes(e.target.value)}
                            placeholder="Rationale, redline guidance, or escalation note..."
                            className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs"
                          />
                        </div>
                        <div className="flex justify-end gap-2">
                          <button
                            type="button"
                            onClick={() => setEditingId(null)}
                            className="rounded border border-slate-300 bg-white px-3 py-1 text-xs text-slate-700"
                          >
                            Cancel
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleReview(finding, 'approved', true)}
                            className="rounded bg-emerald-600 px-3 py-1 text-xs font-semibold text-white hover:bg-emerald-500"
                          >
                            Save &amp; Approve
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleReview(finding, 'flagged', true)}
                            className="rounded bg-rose-600 px-3 py-1 text-xs font-semibold text-white hover:bg-rose-500"
                          >
                            Save &amp; Flag
                          </button>
                        </div>
                      </div>
                    ) : (
                      <p className="whitespace-pre-wrap text-xs leading-relaxed text-slate-700">
                        {finding.edited_summary || finding.summary}
                      </p>
                    )}

                    {finding.reviewer_name && (
                      <p className="text-[11px] text-slate-500">
                        Last reviewed by <strong>{finding.reviewer_name}</strong>
                        {finding.reviewer_notes ? ` — "${finding.reviewer_notes}"` : ''}
                      </p>
                    )}

                    {finding.citations_json.length > 0 && (
                      <div className="space-y-1.5">
                        {finding.citations_json.map((c, idx) => (
                          <CitationBadge key={`${finding.id}-rq-${idx}`} citation={c} index={idx} />
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* IMMUTABLE AUDIT TRAIL */}
      {view === 'audit' && (
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
          {auditLogs.length === 0 ? (
            <div className="p-10 text-center text-xs text-slate-500">
              No audit log records yet.
            </div>
          ) : (
            <div className="divide-y divide-slate-100">
              {auditLogs.map((entry) => {
                const expanded = expandedAuditId === entry.id;
                return (
                  <div key={entry.id} className="p-4 text-xs">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <button
                          type="button"
                          onClick={() =>
                            setExpandedAuditId(expanded ? null : entry.id)
                          }
                          className="inline-flex items-center gap-1 font-semibold text-slate-900 hover:text-indigo-700"
                        >
                          {expanded ? (
                            <ChevronDown className="h-3.5 w-3.5" />
                          ) : (
                            <ChevronRight className="h-3.5 w-3.5" />
                          )}
                          {entry.action}
                        </button>
                        <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-700">
                          {entry.actor_type}: {entry.actor_id}
                        </span>
                        {entry.provider && (
                          <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] font-medium text-indigo-800">
                            {entry.provider}/{entry.model_name}
                          </span>
                        )}
                        {entry.prompt_template && (
                          <span className="font-mono text-[10px] text-slate-500">
                            {entry.prompt_template} v{entry.prompt_version}
                          </span>
                        )}
                        {entry.escalated && (
                          <span className="rounded bg-rose-50 px-1.5 py-0.5 text-[10px] font-semibold text-rose-700">
                            Escalated
                          </span>
                        )}
                      </div>

                      <div className="flex items-center gap-3 text-[11px] text-slate-500">
                        {entry.grounding_score !== null && (
                          <span>Grounding {(entry.grounding_score * 100).toFixed(0)}%</span>
                        )}
                        {entry.latency_ms !== null && <span>{entry.latency_ms}ms</span>}
                        <span>{new Date(entry.created_at).toLocaleString()}</span>
                      </div>
                    </div>

                    {entry.notes && (
                      <p className="mt-1 pl-5 text-slate-600">{entry.notes}</p>
                    )}

                    {(entry.previous_state || entry.new_state) && (
                      <p className="mt-1 pl-5 font-mono text-[11px] text-slate-500">
                        Transition: {entry.previous_state ?? '—'} → {entry.new_state ?? '—'}
                      </p>
                    )}

                    {expanded && (
                      <div className="mt-3 ml-5 space-y-2 rounded-lg border border-slate-200 bg-slate-50 p-3">
                        {entry.retrieved_spans_json.length > 0 && (
                          <div>
                            <p className="text-[11px] font-semibold text-slate-700">
                              Retrieved Source Spans ({entry.retrieved_spans_json.length})
                            </p>
                            <div className="mt-1 flex flex-wrap gap-1.5">
                              {entry.retrieved_spans_json.map((span, idx) => (
                                <span
                                  key={`sp-${idx}`}
                                  className="rounded border border-slate-200 bg-white px-2 py-0.5 font-mono text-[10px] text-slate-700"
                                >
                                  [{String(span.chunk_ref || idx + 1)}]{' '}
                                  {String(span.document_filename || '')} p.
                                  {String(span.page_number ?? '?')} ¶
                                  {String(span.paragraph_index ?? '?')}
                                </span>
                              ))}
                            </div>
                          </div>
                        )}
                        {entry.prompt_text && (
                          <div>
                            <p className="text-[11px] font-semibold text-slate-700">
                              Logged Prompt Payload
                            </p>
                            <pre className="mt-1 max-h-56 overflow-y-auto whitespace-pre-wrap rounded border border-slate-200 bg-white p-2 font-mono text-[10px] text-slate-700">
                              {entry.prompt_text}
                            </pre>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
