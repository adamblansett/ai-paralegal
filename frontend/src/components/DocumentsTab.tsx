import { useEffect, useRef, useState, type ChangeEvent, type DragEvent } from 'react';
import {
  BookOpen,
  CloudDownload,
  Download,
  Eye,
  FileText,
  FolderGit2,
  Loader2,
  RefreshCw,
  Shield,
  ShieldAlert,
  Stamp,
  Trash2,
  UploadCloud,
} from 'lucide-react';
import { api } from '../api/client';
import type {
  DMSFileItem,
  DMSProviderInfo,
  DocumentChunkItem,
  DocumentItem,
  PrivilegeStatus,
} from '../types/api';

interface DocumentsTabProps {
  matterId: string;
  documents: DocumentItem[];
  canWrite?: boolean;
  legalHoldActive?: boolean;
  onOpenDocument?: (doc: DocumentItem) => void;
  onRefresh: () => Promise<void>;
}

const STATUS_STYLE: Record<string, string> = {
  indexed: 'bg-emerald-50 text-emerald-800 border-emerald-200',
  processing: 'bg-sky-50 text-sky-800 border-sky-200',
  pending: 'bg-amber-50 text-amber-800 border-amber-200',
  failed: 'bg-rose-50 text-rose-800 border-rose-200',
};

const PRIVILEGE_LABELS: Record<PrivilegeStatus, string> = {
  none: 'Not Privileged',
  attorney_client: 'Attorney-Client',
  work_product: 'Work Product',
  confidential: 'Confidential',
  privileged_and_confidential: 'Privileged & Conf.',
};

export function DocumentsTab({
  matterId,
  documents,
  canWrite = true,
  legalHoldActive = false,
  onOpenDocument,
  onRefresh,
}: DocumentsTabProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [documentType, setDocumentType] = useState('general');
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [inspectDoc, setInspectDoc] = useState<DocumentItem | null>(null);
  const [chunks, setChunks] = useState<DocumentChunkItem[]>([]);
  const [loadingChunks, setLoadingChunks] = useState(false);

  const [batesPrefix, setBatesPrefix] = useState('PROD');
  const [batesStart, setBatesStart] = useState(1);
  const [stamping, setStamping] = useState(false);

  const [dmsOpen, setDmsOpen] = useState(false);
  const [dmsProviders, setDmsProviders] = useState<DMSProviderInfo[]>([]);
  const [dmsFiles, setDmsFiles] = useState<DMSFileItem[]>([]);
  const [loadingDms, setLoadingDms] = useState(false);
  const [importingFileId, setImportingFileId] = useState<string | null>(null);

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

  async function handlePrivilegeChange(docId: string, privilegeStatus: PrivilegeStatus) {
    setError(null);
    try {
      await api.updateDocumentPrivilege(docId, {
        privilege_status: privilegeStatus,
        privilege_reason:
          privilegeStatus === 'none'
            ? undefined
            : `Designated ${PRIVILEGE_LABELS[privilegeStatus]} via Matter Registry`,
      });
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update privilege classification.');
    }
  }

  async function handleBatesStamp() {
    setError(null);
    setStamping(true);
    try {
      const res = await api.batesStampDocuments(matterId, {
        prefix: batesPrefix.trim() || 'PROD',
        start_number: batesStart,
        zero_pad: 6,
      });
      setBatesStart(res.next_number);
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Bates stamping failed.');
    } finally {
      setStamping(false);
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

  async function handleToggleDms() {
    const next = !dmsOpen;
    setDmsOpen(next);
    if (!next) return;
    setLoadingDms(true);
    setError(null);
    try {
      const [provs, files] = await Promise.all([
        api.listDMSProviders(),
        api.listDMSFiles('mock_dms'),
      ]);
      setDmsProviders(provs);
      setDmsFiles(files);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load DMS connectors.');
    } finally {
      setLoadingDms(false);
    }
  }

  async function handleImportFromDms(item: DMSFileItem) {
    setImportingFileId(item.external_id);
    setError(null);
    try {
      await api.importDMSFile(matterId, 'mock_dms', item.external_id, item.document_type);
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'DMS import failed.');
    } finally {
      setImportingFileId(null);
    }
  }

  const privilegedCount = documents.filter(
    (d) => d.privilege_status && d.privilege_status !== 'none'
  ).length;

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
              disabled={uploading || !canWrite}
              onClick={() => fileInputRef.current?.click()}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:opacity-50"
            >
              <UploadCloud className="h-4 w-4" />
              {uploading ? 'Uploading...' : canWrite ? 'Select Files' : 'Read-Only'}
            </button>

            <button
              type="button"
              onClick={() => void handleToggleDms()}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50"
            >
              <FolderGit2 className="h-4 w-4 text-indigo-600" />
              Import from DMS
            </button>
          </div>
        </div>
      </div>

      {/* Collapsible DMS Connector Drawer */}
      {dmsOpen && (
        <div className="rounded-xl border border-indigo-200 bg-white p-5 shadow-sm space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h4 className="text-sm font-semibold text-slate-900">
                Enterprise DMS &amp; Legal Vault Connectors
              </h4>
              <p className="text-xs text-slate-500">
                Import contracts and discovery emails directly with full audit trail provenance.
              </p>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {dmsProviders.map((prov) => (
                <span
                  key={prov.provider}
                  className={`rounded-full px-2.5 py-0.5 text-[10px] font-semibold ${
                    prov.configured
                      ? 'bg-emerald-50 text-emerald-800 ring-1 ring-emerald-200'
                      : 'bg-slate-100 text-slate-500'
                  }`}
                >
                  {prov.display_name} {prov.configured ? '✓' : '(Env Config Required)'}
                </span>
              ))}
            </div>
          </div>

          {loadingDms ? (
            <div className="flex items-center gap-2 py-4 text-xs text-slate-500">
              <Loader2 className="h-4 w-4 animate-spin text-indigo-600" />
              Listing files from Built-In Legal Fixture Vault...
            </div>
          ) : (
            <div className="grid gap-2 sm:grid-cols-2">
              {dmsFiles.map((item) => (
                <div
                  key={item.external_id}
                  className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 bg-slate-50 px-3.5 py-2.5 text-xs"
                >
                  <div className="min-w-0">
                    <p className="truncate font-semibold text-slate-900">{item.name}</p>
                    <p className="text-[11px] text-slate-500">
                      {item.document_type} • {(item.size_bytes / 1024).toFixed(1)} KB
                    </p>
                  </div>
                  <button
                    type="button"
                    disabled={!canWrite || importingFileId === item.external_id}
                    onClick={() => void handleImportFromDms(item)}
                    className="inline-flex shrink-0 items-center gap-1 rounded-lg bg-indigo-600 px-3 py-1.5 text-[11px] font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
                  >
                    {importingFileId === item.external_id ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <CloudDownload className="h-3 w-3" />
                    )}
                    Import
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Discovery Production Bar: Bates Stamping & Privilege Log */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-5 py-3.5 shadow-sm">
        <div className="flex flex-wrap items-center gap-2.5 text-xs">
          <span className="inline-flex items-center gap-1.5 font-semibold text-slate-800">
            <Stamp className="h-4 w-4 text-indigo-600" />
            Bates Numbering
          </span>
          <input
            type="text"
            value={batesPrefix}
            onChange={(e) => setBatesPrefix(e.target.value.toUpperCase())}
            aria-label="Bates prefix"
            placeholder="PROD"
            className="w-24 rounded-lg border border-slate-300 px-2.5 py-1 font-mono text-xs text-slate-800"
          />
          <input
            type="number"
            min={1}
            value={batesStart}
            onChange={(e) => setBatesStart(Math.max(1, Number(e.target.value) || 1))}
            aria-label="Bates start number"
            className="w-24 rounded-lg border border-slate-300 px-2.5 py-1 font-mono text-xs text-slate-800"
          />
          <button
            type="button"
            disabled={!canWrite || stamping || documents.length === 0}
            onClick={() => void handleBatesStamp()}
            className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-semibold text-white hover:bg-slate-800 disabled:opacity-50"
          >
            {stamping ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Stamp className="h-3.5 w-3.5" />}
            Apply Bates Stamps
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-medium text-slate-700">
            <Shield className="h-3 w-3 text-rose-600" />
            Privileged Docs: {privilegedCount}
          </span>
          <a
            href={api.exportPrivilegeLogUrl(matterId, 'csv')}
            className="inline-flex items-center gap-1 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
          >
            <Download className="h-3.5 w-3.5" />
            Privilege Log CSV
          </a>
          <a
            href={api.exportPrivilegeLogUrl(matterId, 'docx')}
            className="inline-flex items-center gap-1 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
          >
            <Download className="h-3.5 w-3.5" />
            Privilege Log DOCX
          </a>
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
                  <th className="px-4 py-3">Bates / Guardrails</th>
                  <th className="px-4 py-3">Privilege</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Provenance</th>
                  <th className="px-4 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {documents.map((doc) => {
                  const privStatus = (doc.privilege_status || 'none') as PrivilegeStatus;
                  const piiCount = doc.pii_detected_count ?? 0;
                  return (
                    <tr key={doc.id} className="hover:bg-slate-50/70">
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2 font-medium text-slate-900">
                          <FileText className="h-4 w-4 text-slate-400" />
                          <span>{doc.filename}</span>
                        </div>
                        <p className="mt-0.5 text-[11px] text-slate-400">
                          <span className="capitalize">{doc.document_type}</span> •{' '}
                          {(doc.file_size_bytes / 1024).toFixed(1)} KB • {doc.sha256_hash.slice(0, 8)}
                        </p>
                      </td>
                      <td className="px-4 py-3 space-y-1">
                        {doc.bates_start ? (
                          <span className="inline-block rounded bg-slate-100 px-2 py-0.5 font-mono text-[10px] font-semibold text-slate-800">
                            {doc.bates_start}
                            {doc.bates_end && doc.bates_end !== doc.bates_start
                              ? `–${doc.bates_end}`
                              : ''}
                          </span>
                        ) : (
                          <span className="text-[11px] text-slate-400">Unstamped</span>
                        )}
                        {piiCount > 0 && (
                          <div>
                            <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[10px] font-semibold text-amber-800 ring-1 ring-amber-200">
                              <ShieldAlert className="h-3 w-3" />
                              {piiCount} PII
                            </span>
                          </div>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {canWrite ? (
                          <select
                            aria-label={`Privilege status for ${doc.filename}`}
                            value={privStatus}
                            onChange={(e) =>
                              void handlePrivilegeChange(doc.id, e.target.value as PrivilegeStatus)
                            }
                            className={`rounded-lg border px-2 py-1 text-[11px] font-medium ${
                              privStatus === 'none'
                                ? 'border-slate-200 bg-white text-slate-600'
                                : 'border-rose-300 bg-rose-50 text-rose-900'
                            }`}
                          >
                            {Object.entries(PRIVILEGE_LABELS).map(([val, lbl]) => (
                              <option key={val} value={val}>
                                {lbl}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <span className="rounded bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-700">
                            {PRIVILEGE_LABELS[privStatus]}
                          </span>
                        )}
                      </td>
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
                        {Boolean(doc.metadata_json?.ocr_applied) && (
                          <span className="ml-1.5 rounded-full bg-indigo-50 px-2 py-0.5 text-[10px] font-semibold text-indigo-800 ring-1 ring-indigo-200">
                            OCR ({String(doc.metadata_json?.ocr_backend ?? 'applied')})
                          </span>
                        )}
                        {Boolean(doc.metadata_json?.ocr_required) && (
                          <span className="ml-1.5 rounded-full bg-amber-50 px-2 py-0.5 text-[10px] font-semibold text-amber-800 ring-1 ring-amber-200">
                            Needs OCR
                          </span>
                        )}
                        {doc.error_message && (
                          <p className="mt-1 max-w-xs truncate text-[11px] text-rose-600">
                            {doc.error_message}
                          </p>
                        )}
                      </td>
                      <td className="px-4 py-3 text-slate-600">
                        {doc.page_count}p • {doc.word_count}w • {doc.chunk_count} chunks
                      </td>
                      <td className="px-4 py-3 text-right">
                        <div className="inline-flex items-center gap-1.5">
                          {onOpenDocument && (
                            <button
                              type="button"
                              onClick={() => onOpenDocument(doc)}
                              className="inline-flex items-center gap-1 rounded border border-indigo-200 bg-indigo-50 px-2 py-1 text-[11px] font-medium text-indigo-800 hover:bg-indigo-100"
                            >
                              <BookOpen className="h-3 w-3" />
                              Reader
                            </button>
                          )}
                          <button
                            type="button"
                            onClick={() => void handleInspect(doc)}
                            className="inline-flex items-center gap-1 rounded border border-slate-200 px-2 py-1 text-[11px] font-medium text-slate-700 hover:bg-slate-100"
                          >
                            <Eye className="h-3 w-3" />
                            Chunks
                          </button>
                          {canWrite && (
                            <>
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
                                disabled={Boolean(doc.legal_hold_active || legalHoldActive)}
                                onClick={() => void handleDelete(doc.id)}
                                title={
                                  doc.legal_hold_active || legalHoldActive
                                    ? 'Deletion locked under active Litigation Legal Hold'
                                    : 'Delete document'
                                }
                                className="rounded border border-rose-200 p-1 text-rose-600 hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-40"
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
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
