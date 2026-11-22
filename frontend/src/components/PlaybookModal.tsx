import { useState, type FormEvent } from 'react';
import { BookOpen, X } from 'lucide-react';
import { api } from '../api/client';
import type { Playbook, SeverityLevel } from '../types/api';

interface PlaybookModalProps {
  open: boolean;
  onClose: () => void;
  onCreated: (pb: Playbook) => void;
}

export function PlaybookModal({ open, onClose, onCreated }: PlaybookModalProps) {
  const [name, setName] = useState('');
  const [practiceArea, setPracticeArea] = useState('Commercial Contracts');
  const [clauseType, setClauseType] = useState('limitation_of_liability');
  const [standardPosition, setStandardPosition] = useState(
    'Mutual aggregate liability cap equal to 12 months of fees with standard carve-outs.'
  );
  const [fallbackPosition, setFallbackPosition] = useState(
    'EXCEPT FOR INDEMNITY, CONFIDENTIALITY, OR GROSS NEGLIGENCE, LIABILITY IS CAPPED AT 12 MONTHS FEES.'
  );
  const [severity, setSeverity] = useState<SeverityLevel>('critical');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) return null;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const created = await api.createPlaybook({
        name,
        practice_area: practiceArea,
        rules: [
          {
            clause_type: clauseType,
            standard_position: standardPosition,
            fallback_position: fallbackPosition,
            red_flag_patterns: [],
            required: true,
            severity_if_deviated: severity,
          },
        ],
      });
      setName('');
      onCreated(created);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save playbook.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 p-4">
      <div className="w-full max-w-lg rounded-xl border border-slate-200 bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
          <div className="flex items-center gap-2">
            <BookOpen className="h-4 w-4 text-indigo-600" />
            <h2 className="text-sm font-semibold text-slate-900">Create Contract Playbook</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-slate-400 hover:bg-slate-100"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-3.5 p-5 text-xs">
          {error && (
            <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-rose-800">
              {error}
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block font-medium text-slate-700">Playbook Name *</label>
              <input
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Enterprise SaaS Buyer Playbook"
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-xs"
              />
            </div>
            <div>
              <label className="block font-medium text-slate-700">Practice Area</label>
              <input
                value={practiceArea}
                onChange={(e) => setPracticeArea(e.target.value)}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-xs"
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block font-medium text-slate-700">Primary Clause Rule</label>
              <select
                value={clauseType}
                onChange={(e) => setClauseType(e.target.value)}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-xs"
              >
                <option value="limitation_of_liability">Limitation of Liability</option>
                <option value="indemnification">Indemnification</option>
                <option value="ip_assignment">IP Assignment</option>
                <option value="confidentiality">Confidentiality</option>
                <option value="termination">Termination</option>
                <option value="renewal_notice">Renewal &amp; Notice</option>
                <option value="data_security_breach">Data Security &amp; Breach</option>
              </select>
            </div>
            <div>
              <label className="block font-medium text-slate-700">Deviation Severity</label>
              <select
                value={severity}
                onChange={(e) => setSeverity(e.target.value as SeverityLevel)}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-xs"
              >
                <option value="critical">Critical</option>
                <option value="high">High</option>
                <option value="medium">Medium</option>
                <option value="low">Low</option>
              </select>
            </div>
          </div>

          <div>
            <label className="block font-medium text-slate-700">Standard Firm Position *</label>
            <textarea
              required
              rows={2}
              value={standardPosition}
              onChange={(e) => setStandardPosition(e.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-xs"
            />
          </div>

          <div>
            <label className="block font-medium text-slate-700">
              Preferred Fallback Redline Clause *
            </label>
            <textarea
              required
              rows={3}
              value={fallbackPosition}
              onChange={(e) => setFallbackPosition(e.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 font-mono text-[11px]"
            />
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg border border-slate-300 px-4 py-2 text-xs text-slate-700"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving}
              className="rounded-lg bg-indigo-600 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
            >
              {saving ? 'Saving...' : 'Save Playbook'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
