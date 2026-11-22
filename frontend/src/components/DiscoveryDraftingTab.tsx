import { useState, type FormEvent } from 'react';
import {
  AlertCircle,
  CalendarClock,
  Copy,
  Download,
  FileEdit,
  GitCompare,
  Loader2,
  Search,
  Sparkles,
} from 'lucide-react';
import { api } from '../api/client';
import type {
  ExtractedFinding,
  HealthResponse,
  LLMProviderName,
  ResearchQueryResponse,
  ReviewTaskItem,
  SearchHit,
  VerifiedCitation,
} from '../types/api';
import { CitationBadge } from './CitationBadge';

interface DiscoveryDraftingTabProps {
  matterId: string;
  findings: ExtractedFinding[];
  tasks: ReviewTaskItem[];
  health: HealthResponse | null;
  onRefresh: () => Promise<void>;
}

type SubView = 'qa' | 'synthesis' | 'drafting';

export function DiscoveryDraftingTab({
  matterId,
  findings,
  tasks,
  health,
  onRefresh,
}: DiscoveryDraftingTabProps) {
  const [subView, setSubView] = useState<SubView>('qa');
  const [provider, setProvider] = useState<LLMProviderName | ''>('');
  const [error, setError] = useState<string | null>(null);

  // Q&A + Search state
  const [question, setQuestion] = useState('');
  const [saveToQueue, setSaveToQueue] = useState(true);
  const [asking, setAsking] = useState(false);
  const [qaResult, setQaResult] = useState<ResearchQueryResponse | null>(null);
  const [searchHits, setSearchHits] = useState<SearchHit[] | null>(null);

  // Synthesis state
  const [synthesizing, setSynthesizing] = useState(false);
  const [synthesisTask, setSynthesisTask] = useState<ReviewTaskItem | null>(null);

  // Drafting state
  const [draftType, setDraftType] = useState<
    | 'internal_memo'
    | 'clause_comparison'
    | 'discovery_requests'
    | 'discovery_responses'
    | 'review_checklist'
  >('internal_memo');
  const [instructions, setInstructions] = useState('');
  const [drafting, setDrafting] = useState(false);
  const [draftTask, setDraftTask] = useState<ReviewTaskItem | null>(null);
  const [copied, setCopied] = useState(false);

  const timelineFindings = findings.filter((f) => f.finding_type === 'timeline_event');
  const entityFindings = findings.filter((f) => f.finding_type === 'entity');
  const contradictionFindings = findings.filter((f) => f.finding_type === 'contradiction');
  const omissionFindings = findings.filter((f) => f.finding_type === 'omission');
  const latestDraftTask =
    draftTask ?? tasks.find((t) => t.task_type === 'draft_generation' && t.status === 'completed') ?? null;

  async function handleAsk(e: FormEvent) {
    e.preventDefault();
    if (!question.trim()) return;
    setError(null);
    setAsking(true);
    setSearchHits(null);
    try {
      const res = await api.researchQuery(matterId, {
        question,
        provider: provider || undefined,
        save_to_queue: saveToQueue,
      });
      setQaResult(res);
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Research query failed.');
    } finally {
      setAsking(false);
    }
  }

  async function handleHybridSearch() {
    if (!question.trim()) return;
    setError(null);
    setAsking(true);
    try {
      const res = await api.searchMatter(matterId, question, 8);
      setSearchHits(res.hits);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Hybrid search failed.');
    } finally {
      setAsking(false);
    }
  }

  async function handleRunSynthesis() {
    setError(null);
    setSynthesizing(true);
    try {
      const task = await api.runDiscoverySynthesis(matterId, {
        provider: provider || undefined,
      });
      setSynthesisTask(task);
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Discovery synthesis failed.');
    } finally {
      setSynthesizing(false);
    }
  }

  async function handleDraft(e: FormEvent) {
    e.preventDefault();
    if (!instructions.trim()) return;
    setError(null);
    setDrafting(true);
    try {
      const task = await api.generateDraft(matterId, {
        draft_type: draftType,
        instructions,
        provider: provider || undefined,
      });
      setDraftTask(task);
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Draft generation failed.');
    } finally {
      setDrafting(false);
    }
  }

  function copyText(text: string) {
    void navigator.clipboard.writeText(text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  }

  const draftResult = latestDraftTask?.result_json as
    | {
        title?: string;
        draft_markdown?: string;
        grounding_score?: number;
        uncertainties?: string[];
        missing_context?: string[];
        citations?: VerifiedCitation[];
        disclaimer?: string;
      }
    | undefined;

  return (
    <div className="space-y-6">
      {error && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-800">
          {error}
        </div>
      )}

      {/* Sub-navigation + Provider Selector */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <div className="flex flex-wrap gap-1.5">
          <button
            type="button"
            onClick={() => setSubView('qa')}
            className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
              subView === 'qa'
                ? 'bg-indigo-600 text-white'
                : 'text-slate-600 hover:bg-slate-100'
            }`}
          >
            <Search className="h-3.5 w-3.5" />
            Q&amp;A &amp; Hybrid Search
          </button>
          <button
            type="button"
            onClick={() => setSubView('synthesis')}
            className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
              subView === 'synthesis'
                ? 'bg-indigo-600 text-white'
                : 'text-slate-600 hover:bg-slate-100'
            }`}
          >
            <GitCompare className="h-3.5 w-3.5" />
            Discovery Synthesis &amp; Chronology
          </button>
          <button
            type="button"
            onClick={() => setSubView('drafting')}
            className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
              subView === 'drafting'
                ? 'bg-indigo-600 text-white'
                : 'text-slate-600 hover:bg-slate-100'
            }`}
          >
            <FileEdit className="h-3.5 w-3.5" />
            Citation-Grounded Drafting
          </button>
        </div>

        <select
          value={provider}
          onChange={(e) => setProvider(e.target.value as LLMProviderName | '')}
          aria-label="Synthesis LLM Provider"
          className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-700"
        >
          <option value="">Default Router ({health?.default_provider ?? 'mock'})</option>
          <option value="mock">Mock (Offline Deterministic)</option>
          <option value="ollama">Ollama (Local Open-Weight)</option>
          <option value="gemini">Gemini (Cloud)</option>
        </select>
      </div>

      {/* Q&A & HYBRID SEARCH */}
      {subView === 'qa' && (
        <div className="space-y-4">
          <form
            onSubmit={handleAsk}
            className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm space-y-3"
          >
            <label className="block text-xs font-semibold text-slate-800">
              Ask a Question Grounded Strictly in Uploaded Matter Documents
            </label>
            <textarea
              rows={2}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="e.g., What annual fee and liability cap were agreed in the emails versus the executed MSA?"
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
            />
            <div className="flex flex-wrap items-center justify-between gap-3">
              <label className="inline-flex items-center gap-2 text-xs text-slate-600">
                <input
                  type="checkbox"
                  checked={saveToQueue}
                  onChange={(e) => setSaveToQueue(e.target.checked)}
                  className="rounded border-slate-300 text-indigo-600"
                />
                Save research answer to Attorney Review Queue
              </label>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  disabled={asking || !question.trim()}
                  onClick={() => void handleHybridSearch()}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                >
                  <Search className="h-3.5 w-3.5" />
                  Hybrid Chunk Search
                </button>
                <button
                  type="submit"
                  disabled={asking || !question.trim()}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
                >
                  {asking ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Sparkles className="h-3.5 w-3.5" />
                  )}
                  Answer with Citations
                </button>
              </div>
            </div>
          </form>

          {qaResult && (
            <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 pb-3">
                <div className="flex items-center gap-2 text-xs">
                  <span className="rounded bg-indigo-50 px-2 py-0.5 font-semibold text-indigo-800">
                    {qaResult.provider}/{qaResult.model_name}
                  </span>
                  <span className="text-slate-500">
                    Confidence {(qaResult.confidence * 100).toFixed(0)}% • Grounding{' '}
                    {(qaResult.grounding_score * 100).toFixed(0)}%
                  </span>
                </div>
                {qaResult.requires_escalation && (
                  <span className="rounded-full bg-rose-50 px-2.5 py-0.5 text-[11px] font-semibold text-rose-700 ring-1 ring-rose-200">
                    Escalated: {qaResult.escalation_reason}
                  </span>
                )}
              </div>

              <div className="whitespace-pre-wrap text-sm leading-relaxed text-slate-800">
                {qaResult.answer}
              </div>

              {(qaResult.uncertainties.length > 0 || qaResult.missing_context.length > 0) && (
                <div className="rounded-lg border border-amber-200 bg-amber-50/70 p-3.5 text-xs text-amber-950 space-y-1.5">
                  <p className="flex items-center gap-1.5 font-semibold">
                    <AlertCircle className="h-4 w-4 text-amber-600" />
                    Uncertainties &amp; Missing Context
                  </p>
                  <ul className="list-disc pl-5 space-y-1">
                    {qaResult.uncertainties.map((u, i) => (
                      <li key={`unc-${i}`}>{u}</li>
                    ))}
                    {qaResult.missing_context.map((m, i) => (
                      <li key={`miss-${i}`}>{m}</li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="space-y-2">
                <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Verified Source Citations ({qaResult.citations.length})
                </h4>
                {qaResult.citations.map((cit, idx) => (
                  <CitationBadge key={`qa-cit-${idx}`} citation={cit} index={idx} />
                ))}
              </div>

              <p className="border-t border-slate-100 pt-2 text-[11px] text-slate-500">
                {qaResult.disclaimer}
              </p>
            </div>
          )}

          {searchHits && (
            <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm space-y-3">
              <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                Hybrid RRF Search Hits ({searchHits.length})
              </h4>
              {searchHits.map((hit) => (
                <div
                  key={hit.chunk_id}
                  className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs space-y-1"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] font-medium text-slate-700">
                    <span>
                      {hit.document_filename} • p.{hit.page_number ?? 1} • ¶
                      {hit.paragraph_index ?? 1}
                      {hit.section_heading ? ` • ${hit.section_heading}` : ''}
                    </span>
                    <span className="font-mono text-[10px] text-slate-500">
                      RRF {hit.rrf_score.toFixed(4)} (lex {hit.lexical_score.toFixed(2)} | sem{' '}
                      {hit.semantic_score.toFixed(2)})
                    </span>
                  </div>
                  <p className="whitespace-pre-wrap text-slate-800">{hit.content}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* DISCOVERY SYNTHESIS & CHRONOLOGY */}
      {subView === 'synthesis' && (
        <div className="space-y-5">
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <div>
              <h3 className="text-sm font-semibold text-slate-900">
                Cross-Document Discovery &amp; Evidence Synthesis
              </h3>
              <p className="text-xs text-slate-500">
                Extracts dated chronology events, entities, cross-document contradictions, and
                omitted exhibits across all indexed matter files.
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <a
                href={api.exportReviewTableUrl(matterId, 'csv')}
                className="inline-flex items-center gap-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50"
              >
                <Download className="h-3.5 w-3.5" />
                Review CSV
              </a>
              <a
                href={api.exportChronologyUrl(matterId, 'markdown')}
                className="inline-flex items-center gap-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50"
              >
                <Download className="h-3.5 w-3.5" />
                Chronology MD
              </a>
              <button
                type="button"
                disabled={synthesizing}
                onClick={() => void handleRunSynthesis()}
                className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
              >
                {synthesizing ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <CalendarClock className="h-4 w-4" />
                )}
                {synthesizing ? 'Synthesizing...' : 'Run Discovery Synthesis'}
              </button>
            </div>
          </div>

          {synthesisTask?.result_json &&
            typeof synthesisTask.result_json.executive_summary === 'string' && (
              <div className="rounded-lg border border-indigo-200 bg-indigo-50 px-4 py-3 text-xs text-indigo-950">
                {synthesisTask.result_json.executive_summary}
              </div>
            )}

          {/* Contradictions & Omissions */}
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="rounded-xl border border-rose-200 bg-white p-5 shadow-sm space-y-3">
              <h4 className="text-xs font-bold uppercase tracking-wide text-rose-700">
                Contradictions Detected ({contradictionFindings.length})
              </h4>
              {contradictionFindings.length === 0 ? (
                <p className="text-xs text-slate-500">No contradictions extracted yet.</p>
              ) : (
                contradictionFindings.map((f) => (
                  <div key={f.id} className="rounded-lg border border-rose-100 bg-rose-50/40 p-3 space-y-2">
                    <p className="text-xs font-semibold text-rose-950">{f.title}</p>
                    <p className="text-xs text-slate-700">{f.summary}</p>
                    {f.citations_json.map((c, idx) => (
                      <CitationBadge key={`${f.id}-con-${idx}`} citation={c} index={idx} />
                    ))}
                  </div>
                ))
              )}
            </div>

            <div className="rounded-xl border border-amber-200 bg-white p-5 shadow-sm space-y-3">
              <h4 className="text-xs font-bold uppercase tracking-wide text-amber-800">
                Notable Omissions &amp; Missing Records ({omissionFindings.length})
              </h4>
              {omissionFindings.length === 0 ? (
                <p className="text-xs text-slate-500">No omissions flagged yet.</p>
              ) : (
                omissionFindings.map((f) => (
                  <div key={f.id} className="rounded-lg border border-amber-100 bg-amber-50/40 p-3 space-y-2">
                    <p className="text-xs font-semibold text-amber-950">{f.title}</p>
                    <p className="text-xs text-slate-700">{f.summary}</p>
                    {f.citations_json.map((c, idx) => (
                      <CitationBadge key={`${f.id}-om-${idx}`} citation={c} index={idx} />
                    ))}
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Chronology + Entities */}
          <div className="grid gap-4 lg:grid-cols-3">
            <div className="lg:col-span-2 rounded-xl border border-slate-200 bg-white p-5 shadow-sm space-y-3">
              <h4 className="text-xs font-bold uppercase tracking-wide text-slate-600">
                Extracted Chronology ({timelineFindings.length})
              </h4>
              {timelineFindings.length === 0 ? (
                <p className="text-xs text-slate-500">
                  Run Discovery Synthesis to build a citation-backed chronology.
                </p>
              ) : (
                <div className="space-y-3">
                  {timelineFindings.map((ev) => (
                    <div
                      key={ev.id}
                      className="border-l-2 border-indigo-400 pl-3.5 space-y-1.5"
                    >
                      <div className="flex items-center gap-2">
                        <span className="rounded bg-indigo-50 px-2 py-0.5 text-[11px] font-semibold text-indigo-800">
                          {String(ev.metadata_json?.date_label || 'Dated Event')}
                        </span>
                        <span className="text-xs font-semibold text-slate-900">{ev.title}</span>
                      </div>
                      <p className="text-xs text-slate-700">{ev.summary}</p>
                      {ev.citations_json.map((c, idx) => (
                        <CitationBadge key={`${ev.id}-tl-${idx}`} citation={c} index={idx} />
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm space-y-3">
              <h4 className="text-xs font-bold uppercase tracking-wide text-slate-600">
                Extracted Entities ({entityFindings.length})
              </h4>
              {entityFindings.length === 0 ? (
                <p className="text-xs text-slate-500">No entities extracted yet.</p>
              ) : (
                <div className="space-y-2">
                  {entityFindings.map((ent) => (
                    <div
                      key={ent.id}
                      className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2 text-xs"
                    >
                      <p className="font-semibold text-slate-900">{ent.title}</p>
                      <p className="text-[11px] text-slate-600">{ent.summary}</p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* CITATION-GROUNDED DRAFTING */}
      {subView === 'drafting' && (
        <div className="grid gap-5 lg:grid-cols-5">
          <form
            onSubmit={handleDraft}
            className="lg:col-span-2 rounded-xl border border-slate-200 bg-white p-5 shadow-sm space-y-4 h-fit"
          >
            <h3 className="text-sm font-semibold text-slate-900">
              Generate Citation-Grounded Draft
            </h3>
            <div>
              <label className="block text-xs font-medium text-slate-700">Work Product Type</label>
              <select
                value={draftType}
                onChange={(e) => setDraftType(e.target.value as typeof draftType)}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-xs"
              >
                <option value="internal_memo">Internal Legal Ops Memo</option>
                <option value="clause_comparison">Clause Comparison Summary</option>
                <option value="discovery_requests">Supplemental Discovery Requests (RFP)</option>
                <option value="discovery_responses">Discovery Responses &amp; Objections</option>
                <option value="review_checklist">Attorney Review Checklist</option>
              </select>
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-700">
                Drafting Instructions *
              </label>
              <textarea
                required
                rows={5}
                value={instructions}
                onChange={(e) => setInstructions(e.target.value)}
                placeholder="e.g., Draft an internal partner memo summarizing the fee discrepancy ($185k vs $240k), uncapped client indemnity, and missing Exhibit B SLA."
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-xs focus:border-indigo-500 focus:outline-none"
              />
            </div>

            <button
              type="submit"
              disabled={drafting || !instructions.trim()}
              className="w-full inline-flex items-center justify-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2.5 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
            >
              {drafting ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <FileEdit className="h-4 w-4" />
              )}
              {drafting ? 'Generating Draft...' : 'Generate Grounded Draft'}
            </button>
          </form>

          <div className="lg:col-span-3 rounded-xl border border-slate-200 bg-white p-5 shadow-sm space-y-4">
            {!draftResult?.draft_markdown ? (
              <div className="py-16 text-center text-xs text-slate-500">
                Generated drafts are automatically queued for licensed attorney verification and
                cite exact source spans.
              </div>
            ) : (
              <>
                <div className="flex items-center justify-between border-b border-slate-100 pb-3">
                  <div>
                    <h4 className="text-sm font-semibold text-slate-900">
                      {draftResult.title || 'Generated Draft'}
                    </h4>
                    <p className="text-[11px] text-slate-500">
                      Grounding {((draftResult.grounding_score ?? 0) * 100).toFixed(0)}% • Added
                      to Attorney Review Queue
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => copyText(draftResult.draft_markdown || '')}
                    className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
                  >
                    <Copy className="h-3.5 w-3.5" />
                    {copied ? 'Copied' : 'Copy Draft'}
                  </button>
                </div>

                <pre className="whitespace-pre-wrap rounded-lg border border-slate-200 bg-slate-50 p-4 font-sans text-xs leading-relaxed text-slate-800">
                  {draftResult.draft_markdown}
                </pre>

                {((draftResult.uncertainties?.length ?? 0) > 0 ||
                  (draftResult.missing_context?.length ?? 0) > 0) && (
                  <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-950">
                    <p className="font-semibold">Uncertainties &amp; Missing Context</p>
                    <ul className="mt-1 list-disc pl-5 space-y-0.5">
                      {(draftResult.uncertainties ?? []).map((u, idx) => (
                        <li key={`du-${idx}`}>{u}</li>
                      ))}
                      {(draftResult.missing_context ?? []).map((m, idx) => (
                        <li key={`dm-${idx}`}>{m}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {(draftResult.citations?.length ?? 0) > 0 && (
                  <div className="space-y-1.5">
                    <h5 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                      Source Citations
                    </h5>
                    {(draftResult.citations ?? []).map((cit, idx) => (
                      <CitationBadge key={`dc-${idx}`} citation={cit} index={idx} />
                    ))}
                  </div>
                )}

                {draftResult.disclaimer && (
                  <p className="text-[11px] text-slate-500">{draftResult.disclaimer}</p>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
