import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Download,
  ExternalLink,
  FileText,
  Loader2,
  Shield,
  ShieldAlert,
  Stamp,
  X,
} from 'lucide-react';
import { api } from '../api/client';
import type { DocumentChunkItem, DocumentItem, SourceViewerTarget } from '../types/api';

interface SourceViewerDrawerProps {
  target: SourceViewerTarget | null;
  onClose: () => void;
}

function renderHighlightedText(text: string, quote: string | null | undefined) {
  const cleaned = quote?.trim();
  if (!cleaned) {
    return <span>{text}</span>;
  }

  const lowerText = text.toLowerCase();
  const lowerQuote = cleaned.toLowerCase();
  const exactIdx = lowerText.indexOf(lowerQuote);
  if (exactIdx !== -1) {
    const before = text.slice(0, exactIdx);
    const match = text.slice(exactIdx, exactIdx + cleaned.length);
    const after = text.slice(exactIdx + cleaned.length);
    return (
      <>
        {before}
        <mark
          data-testid="citation-highlight"
          className="rounded bg-amber-200 px-0.5 font-semibold text-slate-950"
        >
          {match}
        </mark>
        {after}
      </>
    );
  }

  // Fallback: try whitespace-collapsed word prefix highlight if quote spans line breaks
  const words = cleaned.split(/\s+/).filter((w) => w.length >= 4);
  if (words.length >= 2) {
    const firstWord = words[0];
    const startIdx = lowerText.indexOf(firstWord.toLowerCase());
    if (startIdx !== -1) {
      const approxLen = Math.min(text.length - startIdx, cleaned.length + 24);
      const before = text.slice(0, startIdx);
      const match = text.slice(startIdx, startIdx + approxLen);
      const after = text.slice(startIdx + approxLen);
      return (
        <>
          {before}
          <mark
            data-testid="citation-highlight"
            className="rounded bg-amber-200 px-0.5 font-semibold text-slate-950"
          >
            {match}
          </mark>
          {after}
        </>
      );
    }
  }

  return <span>{text}</span>;
}

export function SourceViewerDrawer({ target, onClose }: SourceViewerDrawerProps) {
  const [doc, setDoc] = useState<DocumentItem | null>(null);
  const [chunks, setChunks] = useState<DocumentChunkItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<'reader' | 'raw'>('reader');
  const activeChunkRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!target) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setViewMode('reader');

    Promise.all([api.getDocument(target.documentId), api.getDocumentChunks(target.documentId)])
      .then(([docData, chunkData]) => {
        if (cancelled) return;
        setDoc(docData);
        setChunks(chunkData);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : 'Unable to load source document.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [target]);

  const highlightedChunkId = useMemo(() => {
    if (!target) return null;
    if (target.chunkId && chunks.some((c) => c.id === target.chunkId)) {
      return target.chunkId;
    }
    if (target.quote) {
      const q = target.quote.trim().toLowerCase();
      const matched = chunks.find((c) => c.content.toLowerCase().includes(q));
      if (matched) return matched.id;
    }
    return chunks[0]?.id ?? null;
  }, [chunks, target]);

  useEffect(() => {
    const el = activeChunkRef.current;
    if (el && typeof el.scrollIntoView === 'function') {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, [highlightedChunkId, viewMode]);

  if (!target) return null;

  const isPdf =
    doc?.content_type.includes('pdf') ||
    (doc?.filename ?? target.filename ?? '').toLowerCase().endsWith('.pdf');

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-slate-950/50 backdrop-blur-[1px]"
      role="dialog"
      aria-modal="true"
      aria-label="Source Document Inspector"
      data-testid="source-viewer-drawer"
    >
      <div className="flex h-full w-full max-w-3xl flex-col bg-white shadow-2xl">
        {/* Header */}
        <div className="flex items-start justify-between gap-4 border-b border-slate-200 px-6 py-4">
          <div className="min-w-0 space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <FileText className="h-4 w-4 text-indigo-600" />
              <h3 className="truncate text-sm font-bold text-slate-900">
                {doc?.filename || target.filename || 'Source Document'}
              </h3>
              {doc?.bates_start && (
                <span className="inline-flex items-center gap-1 rounded bg-slate-100 px-2 py-0.5 font-mono text-[10px] font-semibold text-slate-700">
                  <Stamp className="h-3 w-3 text-slate-500" />
                  {doc.bates_start}
                  {doc.bates_end && doc.bates_end !== doc.bates_start ? `–${doc.bates_end}` : ''}
                </span>
              )}
              {doc?.privilege_status && doc.privilege_status !== 'none' && (
                <span className="inline-flex items-center gap-1 rounded-full bg-rose-50 px-2 py-0.5 text-[10px] font-semibold uppercase text-rose-800 ring-1 ring-rose-200">
                  <Shield className="h-3 w-3" />
                  {doc.privilege_status.replace(/_/g, ' ')}
                </span>
              )}
              {Boolean(doc?.pii_detected_count && doc.pii_detected_count > 0) && (
                <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[10px] font-semibold text-amber-800 ring-1 ring-amber-200">
                  <ShieldAlert className="h-3 w-3" />
                  {doc?.pii_detected_count} PII detected
                </span>
              )}
              {Boolean(doc?.metadata_json?.ocr_applied) && (
                <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-[10px] font-semibold text-indigo-800 ring-1 ring-indigo-200">
                  OCR ({String(doc?.metadata_json?.ocr_backend ?? 'applied')})
                </span>
              )}
            </div>
            <p className="text-xs text-slate-500">
              {target.pageNumber ? `Target p.${target.pageNumber}` : ''}
              {target.paragraphIndex ? ` • ¶${target.paragraphIndex}` : ''}
              {target.sectionHeading ? ` • ${target.sectionHeading}` : ''}
            </p>
          </div>

          <div className="flex shrink-0 items-center gap-2">
            {doc && (
              <a
                href={api.documentContentUrl(doc.id, true)}
                className="inline-flex items-center gap-1 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
              >
                <Download className="h-3.5 w-3.5" />
                Download
              </a>
            )}
            <button
              type="button"
              onClick={onClose}
              aria-label="Close source viewer"
              className="rounded-lg border border-slate-200 p-1.5 text-slate-500 hover:bg-slate-100"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* View switcher & cited quote banner */}
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 bg-slate-50 px-6 py-2.5 text-xs">
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => setViewMode('reader')}
              className={`rounded-md px-3 py-1 font-semibold transition ${
                viewMode === 'reader'
                  ? 'bg-indigo-600 text-white'
                  : 'text-slate-600 hover:bg-slate-200/70'
              }`}
            >
              Structured Reader ({chunks.length} chunks)
            </button>
            <button
              type="button"
              onClick={() => setViewMode('raw')}
              className={`inline-flex items-center gap-1 rounded-md px-3 py-1 font-semibold transition ${
                viewMode === 'raw'
                  ? 'bg-indigo-600 text-white'
                  : 'text-slate-600 hover:bg-slate-200/70'
              }`}
            >
              <ExternalLink className="h-3 w-3" />
              Original File Preview
            </button>
          </div>
          {target.quote && (
            <span className="max-w-sm truncate font-mono text-[11px] text-amber-900">
              Highlighting: &ldquo;{target.quote}&rdquo;
            </span>
          )}
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-6">
          {loading ? (
            <div className="flex items-center justify-center gap-2 py-20 text-xs text-slate-500">
              <Loader2 className="h-4 w-4 animate-spin text-indigo-600" />
              Loading authoritative source document...
            </div>
          ) : error ? (
            <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-800">
              {error}
            </div>
          ) : viewMode === 'raw' && doc ? (
            <div className="h-full min-h-[520px] space-y-2">
              {isPdf ? (
                <iframe
                  title={doc.filename}
                  src={api.documentContentUrl(doc.id, false)}
                  className="h-[640px] w-full rounded-lg border border-slate-200"
                />
              ) : (
                <div className="space-y-2">
                  <p className="text-xs text-slate-500">
                    Raw inline view rendered via authenticated endpoint{' '}
                    <code>/api/v1/documents/{doc.id}/content</code>.
                  </p>
                  <iframe
                    title={doc.filename}
                    src={api.documentContentUrl(doc.id, false)}
                    className="h-[580px] w-full rounded-lg border border-slate-200 bg-white"
                  />
                </div>
              )}
            </div>
          ) : chunks.length === 0 ? (
            <p className="py-12 text-center text-xs text-slate-500">
              No indexed text chunks available for this document yet.
            </p>
          ) : (
            <div className="space-y-3">
              {chunks.map((chunk) => {
                const isTarget = chunk.id === highlightedChunkId;
                const batesNum =
                  typeof chunk.metadata_json?.bates_number === 'string'
                    ? chunk.metadata_json.bates_number
                    : null;
                return (
                  <div
                    key={chunk.id}
                    ref={isTarget ? activeChunkRef : null}
                    data-testid={isTarget ? 'active-source-chunk' : undefined}
                    className={`rounded-xl border p-4 text-xs transition ${
                      isTarget
                        ? 'border-amber-400 bg-amber-50/40 ring-2 ring-amber-200'
                        : 'border-slate-200 bg-white'
                    }`}
                  >
                    <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px] font-medium text-slate-600">
                      <span
                        className={`rounded px-1.5 py-0.5 font-semibold ${
                          isTarget
                            ? 'bg-amber-200 text-amber-950'
                            : 'bg-slate-100 text-slate-700'
                        }`}
                      >
                        Chunk #{chunk.chunk_index}
                      </span>
                      {chunk.page_number !== null && <span>Page {chunk.page_number}</span>}
                      {chunk.paragraph_index !== null && <span>• ¶ {chunk.paragraph_index}</span>}
                      {chunk.section_heading && (
                        <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-indigo-800">
                          {chunk.section_heading}
                        </span>
                      )}
                      {batesNum && (
                        <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-700">
                          {batesNum}
                        </span>
                      )}
                      <span className="ml-auto font-mono text-[10px] text-slate-400">
                        chars {chunk.char_start ?? 0}–{chunk.char_end ?? 0}
                      </span>
                    </div>
                    <p className="whitespace-pre-wrap leading-relaxed text-slate-800">
                      {isTarget
                        ? renderHighlightedText(chunk.content, target.quote)
                        : chunk.content}
                    </p>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
