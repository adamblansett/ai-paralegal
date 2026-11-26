import { useState, type FormEvent } from 'react';
import { ShieldAlert, ShieldCheck, X } from 'lucide-react';
import { api } from '../api/client';
import type { ConflictCheckResponse, MatterCreateInput } from '../types/api';

interface NewMatterModalProps {
  open: boolean;
  onClose: () => void;
  onCreate: (payload: MatterCreateInput) => Promise<void>;
}

export function NewMatterModal({ open, onClose, onCreate }: NewMatterModalProps) {
  const [title, setTitle] = useState('');
  const [clientName, setClientName] = useState('');
  const [counterpartiesInput, setCounterpartiesInput] = useState('');
  const [practiceArea, setPracticeArea] = useState('Commercial Contracts');
  const [jurisdiction, setJurisdiction] = useState('Delaware');
  const [description, setDescription] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [checkingConflicts, setCheckingConflicts] = useState(false);
  const [conflictResult, setConflictResult] = useState<ConflictCheckResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!open) return null;

  async function handleConflictCheck() {
    if (!clientName.trim()) {
      setError('Enter Client Name first to run conflict screening.');
      return;
    }
    setError(null);
    setCheckingConflicts(true);
    try {
      const counterparties = counterpartiesInput
        .split(',')
        .map((c) => c.trim())
        .filter(Boolean);
      const res = await api.runConflictCheck({
        client_name: clientName.trim(),
        counterparties,
        matter_title: title.trim() || undefined,
      });
      setConflictResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Conflict check failed.');
    } finally {
      setCheckingConflicts(false);
    }
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await onCreate({
        title,
        client_name: clientName,
        practice_area: practiceArea,
        jurisdiction: jurisdiction || undefined,
        description: description || undefined,
      });
      setTitle('');
      setClientName('');
      setCounterpartiesInput('');
      setDescription('');
      setConflictResult(null);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create matter.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 p-4">
      <div className="w-full max-w-lg rounded-xl border border-slate-200 bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
          <h2 className="text-base font-semibold text-slate-900">Create Legal Matter</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4 p-5">
          {error && (
            <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">
              {error}
            </div>
          )}

          <div>
            <label className="block text-xs font-medium text-slate-700">Matter Title *</label>
            <input
              required
              minLength={2}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g., Meridian v. Apex Cloud — MSA & Billing Dispute"
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-700">Client Name *</label>
              <input
                required
                minLength={2}
                value={clientName}
                onChange={(e) => setClientName(e.target.value)}
                placeholder="Meridian Legal Group LLP"
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-700">Practice Area</label>
              <select
                value={practiceArea}
                onChange={(e) => setPracticeArea(e.target.value)}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
              >
                <option>Commercial Contracts</option>
                <option>Commercial Litigation</option>
                <option>M&A / Corporate</option>
                <option>Privacy & Cybersecurity</option>
                <option>Employment</option>
                <option>IP Licensing</option>
              </select>
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-700">
              Counterparties / Adverse Parties (comma-separated, optional)
            </label>
            <div className="mt-1 flex gap-2">
              <input
                value={counterpartiesInput}
                onChange={(e) => setCounterpartiesInput(e.target.value)}
                placeholder="e.g., Apex Cloud Systems, Beta Holdings LLC"
                className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
              />
              <button
                type="button"
                data-testid="conflict-check-btn"
                disabled={checkingConflicts}
                onClick={() => void handleConflictCheck()}
                className="rounded-lg border border-indigo-300 bg-indigo-50 px-3 py-2 text-xs font-semibold text-indigo-800 hover:bg-indigo-100 disabled:opacity-50"
              >
                {checkingConflicts ? 'Screening...' : 'Run Conflict Check'}
              </button>
            </div>
          </div>

          {conflictResult && (
            <div
              className={`rounded-lg border px-3 py-2.5 text-xs ${
                conflictResult.has_conflicts
                  ? 'border-amber-300 bg-amber-50 text-amber-950'
                  : 'border-emerald-200 bg-emerald-50 text-emerald-900'
              }`}
            >
              <div className="flex items-center gap-1.5 font-semibold">
                {conflictResult.has_conflicts ? (
                  <ShieldAlert className="h-4 w-4 text-amber-700" />
                ) : (
                  <ShieldCheck className="h-4 w-4 text-emerald-700" />
                )}
                Conflict Screen: {conflictResult.risk_level.toUpperCase()} ({conflictResult.matches.length}{' '}
                match{conflictResult.matches.length === 1 ? '' : 'es'})
              </div>
              <p className="mt-1">{conflictResult.recommended_action}</p>
            </div>
          )}

          <div>
            <label className="block text-xs font-medium text-slate-700">Governing Jurisdiction</label>
            <input
              value={jurisdiction}
              onChange={(e) => setJurisdiction(e.target.value)}
              placeholder="Delaware / New York / Federal"
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-700">Matter Summary / Scope</label>
            <textarea
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Key issues, opposing counsel, or contract review focus..."
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
            />
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg border border-slate-300 px-4 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
            >
              {submitting ? 'Creating...' : 'Create Matter'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
