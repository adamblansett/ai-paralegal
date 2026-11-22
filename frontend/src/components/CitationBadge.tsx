import { useState } from 'react';
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  FileText,
  HelpCircle,
} from 'lucide-react';
import type { VerifiedCitation } from '../types/api';

interface CitationBadgeProps {
  citation: VerifiedCitation;
  index: number;
  onInspectCitation?: (citation: VerifiedCitation) => void;
}

export function CitationBadge({ citation, index, onInspectCitation }: CitationBadgeProps) {
  const [expanded, setExpanded] = useState(false);

  const badgeStyle =
    citation.verification_status === 'verified_exact'
      ? 'border-emerald-200 bg-emerald-50 text-emerald-900 hover:bg-emerald-100'
      : citation.verification_status === 'verified_fuzzy'
      ? 'border-amber-200 bg-amber-50 text-amber-900 hover:bg-amber-100'
      : 'border-rose-200 bg-rose-50 text-rose-900 hover:bg-rose-100';

  const statusLabel =
    citation.verification_status === 'verified_exact'
      ? 'Exact Quote Verified'
      : citation.verification_status === 'verified_fuzzy'
      ? 'Fuzzy Match Verified'
      : 'Unverified Citation';

  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50/70">
      <div className="flex items-stretch gap-1">
        <button
          type="button"
          onClick={() => setExpanded((prev) => !prev)}
          className={`flex min-w-0 flex-1 items-center justify-between gap-2 rounded-lg border px-2.5 py-1.5 text-left text-xs transition ${badgeStyle}`}
        >
          <div className="flex min-w-0 items-center gap-1.5">
            {citation.verification_status === 'unverified' ? (
              <HelpCircle className="h-3.5 w-3.5 shrink-0" />
            ) : (
              <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
            )}
            <FileText className="h-3.5 w-3.5 shrink-0 opacity-70" />
            <span className="truncate font-medium">
              [{citation.chunk_ref || `C${index + 1}`}] {citation.document_filename || 'Source'}
              {citation.page_number !== null ? ` • p.${citation.page_number}` : ''}
              {citation.paragraph_index !== null ? ` • ¶${citation.paragraph_index}` : ''}
              {citation.section_heading ? ` • ${citation.section_heading}` : ''}
            </span>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            <span className="rounded bg-white/75 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide">
              {citation.verification_status.replace('_', ' ')}
            </span>
            {expanded ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" />
            )}
          </div>
        </button>

        {citation.document_id && onInspectCitation && (
          <button
            type="button"
            data-testid="view-in-source-btn"
            onClick={() => onInspectCitation(citation)}
            className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-indigo-200 bg-indigo-50 px-2.5 py-1.5 text-[11px] font-semibold text-indigo-800 hover:bg-indigo-100"
          >
            <ExternalLink className="h-3 w-3" />
            View in Source
          </button>
        )}
      </div>

      {expanded && (
        <div className="space-y-1.5 px-3 py-2 text-xs text-slate-700">
          <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
            <span className="font-semibold text-slate-700">{statusLabel}</span>
            <span>• Match score: {(citation.match_score * 100).toFixed(0)}%</span>
            <span>• Chunk ID: {citation.chunk_id.slice(0, 8)}</span>
            {citation.char_start !== null && citation.char_end !== null && (
              <span>
                • Offset: {citation.char_start}–{citation.char_end}
              </span>
            )}
          </div>
          <blockquote className="rounded border-l-2 border-slate-400 bg-white px-2.5 py-1.5 font-mono text-[11px] leading-relaxed text-slate-800">
            &ldquo;{citation.quote}&rdquo;
          </blockquote>
        </div>
      )}
    </div>
  );
}
