import { useEffect, useRef, useState, type ChangeEvent, type DragEvent } from 'react';
import {
  Eye,
  FileText,
  Loader2,
  RefreshCw,
  Trash2,
  UploadCloud,
} from 'lucide-react';
import { api } from '../api/client';
import type { DocumentChunkItem, DocumentItem } from '../types/api';

interface DocumentsTabProps {
  matterId: string;
  documents: DocumentItem[];
  onRefresh: () => Promise<void>;
}

const STATUS_STYLE: Record<string, string> = {
  indexed: 'bg-emerald-50 text-emerald-800 border-emerald-200',
  processing: 'bg-sky-50 text-sky-800 border-sky-200',
  pending: 'bg-amber-50 text-amber-800 border-amber-200',
  failed: 'bg-rose-50 text-rose-800 border-rose-200',
};

export function DocumentsTab({ matterId, documents, onRefresh }: DocumentsTabProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [documentType, setDocumentType] = useState('general');
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [inspectDoc, setInspectDoc] = useState<DocumentItem | null>(null);
  const [chunks, setChunks] = useState<DocumentChunkItem[]>([]);
  const [loadingChunks, setLoadingChunks] = useState(false);

  const hasInProgress = documents.some(
    (d) => d.ingestion_status === 'pending' || d.ingestion_status === 'processing'
  );

  useEffect(() => {
    if (!hasInProgress) return;
    const timer = window.setInterval(() => {
      void onRefresh();
    }, 2000);
    return () => window.clearInterval(timer);
  }, [hasInProgress, onRefresh]);

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    setError(null);
    setUploading(true);
    try {
      for (const file of Array.from(files)) {
        await api.uploadDocument(matterId, file, documentType);
      }
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Document upload failed.');
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    void handleFiles(e.dataTransfer.files);
  }

  async function handleInspect(doc: DocumentItem) {
    setInspectDoc(doc);
    setLoadingChunks(true);
    try {
      const data = await api.getDocumentChunks(doc.id);
      setChunks(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load chunks.');
    } finally {
      setLoadingChunks(false);
    }
  }

  async function handleReprocess(docId: string) {
    setError(null);
    try {
      await api.reprocessDocument(docId);
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Reprocess failed.');
    }
  }

  async function handleDelete(docId: string) {
    setError(null);
    try {
      await api.deleteDocument(docId);
      if (inspectDoc?.id === docId) {
        setInspectDoc(null);
        setChunks([]);
      }
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed.');
    }
  }

  return (
    <div className="space-y-6">
      {error && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-800">
          {error}
        </div>
      )}

      {/* Upload Dropzone */}
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        className={`rounded-xl border-2 border-dashed p-6 transition ${
          dragOver
            ? 'border-indigo-500 bg-indigo-50/70'
            : 'border-slate-300 bg-white hover:border-slate-400'
        }`}
      >
        <div className="flex flex-col items-center justify-between gap-4 sm:flex-row">
          <div className="flex items-center gap-3.5">
            <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-indigo-50 text-indigo-600">
              {uploading ? (
                <Loader2 className="h-5 w-5 animate-spin" />
              ) : (
                <UploadCloud className="h-5 w-5" />
              )}
            </div>
            <div>
              <h3 className="text-sm font-semibold text-slate-900">
                Ingest Legal Documents &amp; Discovery Exports
              </h3>
              <p className="text-xs text-slate-500">
                Supports PDF, DOCX, TXT, Markdown, EML email threads, and deposition/call transcripts
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2.5">
            <select
              value={documentType}
              onChange={(e) => setDocumentType(e.target.value)}
              aria-label="Document classification"
              className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs text-slate-700 focus:border-indigo-500 focus:outline-none"
            >
              <option value="general">Auto-Detect Type</option>
              <option value="contract">Contract / Agreement</option>
              <option value="email">Email / Correspondence</option>
              <option value="transcript">Transcript</option>
              <option value="pleading">Pleading / Filing</option>
              <option value="memo">Legal Memo</option>
            </select>

            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept=".pdf,.docx,.txt,.md,.eml,.vtt,.srt"
              onChange={(e: ChangeEvent<HTMLInputElement>) => void handleFiles(e.target.files)}
              className="hidden"
            />
            <button
              type="button"
              disabled={uploading}
              onClick={() => fileInputRef.current?.click()}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:opacity-50"
            >
              <UploadCloud className="h-4 w-4" />
              {uploading ? 'Uploading...' : 'Select Files'}
            </button>
          </div>
        </div>
      </div>

      {/* Document Registry Table */}
      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3.5">
          <h3 className="text-sm font-semibold text-slate-900">
            Matter Document Index ({documents.length})
          </h3>
          <button
            type="button"
            onClick={() => void onRefresh()}
            className="inline-flex items-center gap-1 rounded-md border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-50"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Refresh
          </button>
        </div>

        {documents.length === 0 ? (
          <div className="p-10 text-center text-xs text-slate-500">
            No documents uploaded yet. Upload a contract (e.g., <code>samples/sample_msa.txt</code>) or
            discovery thread (<code>samples/sample_discovery_emails.eml</code>) to start.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200 text-left text-xs">
              <thead className="bg-slate-50 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                <tr>
                  <th className="px-4 py-3">Document</th>
                  <th className="px-4 py-3">Type</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Provenance Stats</th>
                  <th className="px-4 py-3">SHA-256</th>
                  <th className="px-4 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {documents.map((doc) => (
                  <tr key={doc.id} className="hover:bg-slate-50/70">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2 font-medium text-slate-900">
                        <FileText className="h-4 w-4 text-slate-400" />
                        <span>{doc.filename}</span>
                      </div>
                      <p className="mt-0.5 text-[11px] text-slate-400">
                        {(doc.file_size_bytes / 1024).toFixed(1)} KB • {doc.storage_backend}
                      </p>
                    </td>
                    <td className="px-4 py-3 capitalize text-slate-700">{doc.document_type}</td>
                    <td className="px-4 py-3">
                      <span
                        className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium capitalize ${
                          STATUS_STYLE[doc.ingestion_status] || 'bg-slate-100 text-slate-700'
                        }`}
                      >
                        {(doc.ingestion_status === 'processing' ||
                          doc.ingestion_status === 'pending') && (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        )}
                        {doc.ingestion_status}
                      </span>
                      {doc.error_message && (
                        <p className="mt-1 max-w-xs truncate text-[11px] text-rose-600">
                          {doc.error_message}
                        </p>
                      )}
                    </td>
                    <td className="px-4 py-3 text-slate-600">
                      {doc.page_count}p • {doc.word_count}w • {doc.chunk_count} chunks
                    </td>
                    <td className="px-4 py-3 font-mono text-[11px] text-slate-500">
                      {doc.sha256_hash.slice(0, 12)}…
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="inline-flex items-center gap-1.5">
                        <button
                          type="button"
                          onClick={() => void handleInspect(doc)}
                          className="inline-flex items-center gap-1 rounded border border-slate-200 px-2 py-1 text-[11px] font-medium text-slate-700 hover:bg-slate-100"
                        >
                          <Eye className="h-3 w-3" />
                          Chunks
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleReprocess(doc.id)}
                          title="Reprocess document"
                          className="rounded border border-slate-200 p-1 text-slate-600 hover:bg-slate-100"
                        >
                          <RefreshCw className="h-3.5 w-3.5" />
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleDelete(doc.id)}
                          title="Delete document"
                          className="rounded border border-rose-200 p-1 text-rose-600 hover:bg-rose-50"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Provenance Chunk Inspector */}
      {inspectDoc && (
        <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-center justify-between border-b border-slate-200 pb-3">
            <div>
              <h4 className="text-sm font-semibold text-slate-900">
                Provenance Inspector — {inspectDoc.filename}
              </h4>
              <p className="text-xs text-slate-500">
                Showing {chunks.length} indexed chunk(s) with page, paragraph, and section spans
              </p>
            </div>
            <button
              type="button"
              onClick={() => setInspectDoc(null)}
              className="rounded border border-slate-200 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50"
            >
              Close
            </button>
          </div>

          {loadingChunks ? (
            <div className="flex items-center gap-2 py-6 text-xs text-slate-500">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading provenance chunks...
            </div>
          ) : (
            <div className="mt-4 max-h-96 space-y-3 overflow-y-auto pr-1">
              {chunks.map((chunk) => (
                <div
                  key={chunk.id}
                  className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs"
                >
                  <div className="mb-1.5 flex flex-wrap items-center gap-2 text-[11px] font-medium text-slate-600">
                    <span className="rounded bg-indigo-100 px-1.5 py-0.5 text-indigo-800">
                      Chunk #{chunk.chunk_index}
                    </span>
                    <span>Page {chunk.page_number ?? '—'}</span>
                    <span>• ¶ {chunk.paragraph_index ?? '—'}</span>
                    {chunk.section_heading && (
                      <span className="rounded bg-slate-200 px-1.5 py-0.5 text-slate-800">
                        {chunk.section_heading}
                      </span>
                    )}
                    <span className="ml-auto font-mono text-[10px] text-slate-400">
                      chars {chunk.char_start ?? 0}–{chunk.char_end ?? 0}
                    </span>
                  </div>
                  <p className="whitespace-pre-wrap leading-relaxed text-slate-800">
                    {chunk.content}
                  </p>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
