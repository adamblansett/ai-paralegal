import { useState } from 'react';
import { Briefcase, LogOut, Plus, Scale, Search, ShieldCheck } from 'lucide-react';
import type { HealthResponse, Matter, UserProfile } from '../types/api';

interface MatterSidebarProps {
  matters: Matter[];
  selectedMatterId: string | null;
  onSelectMatter: (matterId: string) => void;
  onNewMatter: () => void;
  onSignOut: () => void;
  currentUser: UserProfile;
  health: HealthResponse | null;
}

export function MatterSidebar({
  matters,
  selectedMatterId,
  onSelectMatter,
  onNewMatter,
  onSignOut,
  currentUser,
  health,
}: MatterSidebarProps) {
  const [search, setSearch] = useState('');

  const filtered = matters.filter((m) => {
    const q = search.trim().toLowerCase();
    if (!q) return true;
    return (
      m.title.toLowerCase().includes(q) ||
      m.client_name.toLowerCase().includes(q) ||
      m.matter_number.toLowerCase().includes(q)
    );
  });

  return (
    <aside className="flex w-80 shrink-0 flex-col border-r border-slate-800 bg-slate-900 text-slate-100">
      <div className="border-b border-slate-800 px-5 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-500/20 text-indigo-300 ring-1 ring-indigo-400/30">
              <Scale className="h-5 w-5" />
            </div>
            <div>
              <h1 className="text-sm font-bold tracking-tight text-white">AI Paralegal</h1>
              <p className="text-[11px] text-slate-400">Legal Ops & Evidence Review</p>
            </div>
          </div>
          {currentUser.role !== 'auditor' && (
            <button
              type="button"
              onClick={onNewMatter}
              className="inline-flex items-center gap-1 rounded-md bg-indigo-600 px-2.5 py-1.5 text-xs font-semibold text-white transition hover:bg-indigo-500"
            >
              <Plus className="h-3.5 w-3.5" />
              New
            </button>
          )}
        </div>

        <div className="relative mt-3.5">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-slate-400" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter matters or clients..."
            className="w-full rounded-md border border-slate-700 bg-slate-800/80 py-1.5 pl-8 pr-3 text-xs text-slate-100 placeholder:text-slate-400 focus:border-indigo-400 focus:outline-none"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-1.5">
        {filtered.length === 0 ? (
          <div className="rounded-lg border border-dashed border-slate-700 p-4 text-center text-xs text-slate-400">
            No matters match your filter. Create a new legal matter to begin.
          </div>
        ) : (
          filtered.map((matter) => {
            const active = matter.id === selectedMatterId;
            return (
              <button
                key={matter.id}
                type="button"
                onClick={() => onSelectMatter(matter.id)}
                className={`w-full rounded-lg border p-3 text-left transition ${
                  active
                    ? 'border-indigo-500/60 bg-slate-800 shadow-sm'
                    : 'border-transparent bg-slate-900 hover:border-slate-700 hover:bg-slate-800/60'
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-[10px] font-semibold uppercase tracking-wider text-indigo-300">
                    {matter.matter_number}
                  </span>
                  <span className="rounded bg-slate-700/70 px-1.5 py-0.5 text-[10px] text-slate-300">
                    {matter.practice_area}
                  </span>
                </div>
                <div className="mt-1 flex items-start gap-1.5">
                  <Briefcase className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-400" />
                  <p className="line-clamp-2 text-xs font-semibold text-white">{matter.title}</p>
                </div>
                <p className="mt-1 truncate text-[11px] text-slate-400">{matter.client_name}</p>
                <div className="mt-2.5 flex flex-wrap items-center gap-1.5 text-[10px]">
                  <span className="rounded bg-slate-800 px-1.5 py-0.5 text-slate-300 ring-1 ring-slate-700">
                    {matter.stats.document_count} docs
                  </span>
                  {matter.stats.pending_review_count > 0 && (
                    <span className="rounded bg-amber-500/20 px-1.5 py-0.5 font-medium text-amber-200 ring-1 ring-amber-400/30">
                      {matter.stats.pending_review_count} pending
                    </span>
                  )}
                  {matter.stats.flagged_count > 0 && (
                    <span className="rounded bg-rose-500/20 px-1.5 py-0.5 font-medium text-rose-200 ring-1 ring-rose-400/30">
                      {matter.stats.flagged_count} flagged
                    </span>
                  )}
                </div>
              </button>
            );
          })
        )}
      </div>

      <div className="border-t border-slate-800 px-4 py-3 text-[11px] text-slate-400 space-y-2.5">
        <div className="flex items-center justify-between gap-2 rounded-lg bg-slate-800/70 px-2.5 py-2">
          <div className="min-w-0">
            <p className="truncate font-semibold text-slate-100">{currentUser.full_name}</p>
            <p className="truncate text-[10px] uppercase tracking-wider text-indigo-300">
              {currentUser.role}
            </p>
          </div>
          <button
            type="button"
            onClick={onSignOut}
            title="Sign out"
            className="rounded border border-slate-700 p-1.5 text-slate-300 hover:bg-slate-700 hover:text-white"
          >
            <LogOut className="h-3.5 w-3.5" />
          </button>
        </div>
        <div>
          <div className="flex items-center justify-between">
            <span className="flex items-center gap-1.5">
              <ShieldCheck className="h-3.5 w-3.5 text-emerald-400" />
              Router: <strong className="text-slate-200">{health?.default_provider ?? '...'}</strong>
            </span>
            <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-300">
              {health?.database === 'ok' ? 'DB Online' : 'Connecting'}
            </span>
          </div>
          {health && (
            <p className="mt-1 truncate text-[10px] text-slate-500">
              OCR: {health.ocr_backend} ({health.tesseract_available ? 'tesseract ready' : 'fallback'})
            </p>
          )}
        </div>
      </div>
    </aside>
  );
}
