import { useCallback, useEffect, useState } from 'react';
import {
  Activity,
  FileCheck2,
  Files,
  GitCompare,
  Loader2,
  Lock,
  Scale,
  ShieldAlert,
  Unlock,
  Users,
} from 'lucide-react';
import { api, tokenStore } from './api/client';
import { AuthGate } from './components/AuthGate';
import { ContractReviewTab } from './components/ContractReviewTab';
import { DisclaimerBanner } from './components/DisclaimerBanner';
import { DiscoveryDraftingTab } from './components/DiscoveryDraftingTab';
import { DocumentsTab } from './components/DocumentsTab';
import { MatterSidebar } from './components/MatterSidebar';
import { NewMatterModal } from './components/NewMatterModal';
import { PlaybookModal } from './components/PlaybookModal';
import { ReviewAuditTab } from './components/ReviewAuditTab';
import { SourceViewerDrawer } from './components/SourceViewerDrawer';
import { TeamAccessModal } from './components/TeamAccessModal';
import { useMatterEvents } from './hooks/useMatterEvents';
import type {
  AuditLogEntry,
  DocumentItem,
  ExtractedFinding,
  HealthResponse,
  Matter,
  MatterCreateInput,
  Playbook,
  ReviewTaskItem,
  SourceViewerTarget,
  UserProfile,
  VerifiedCitation,
} from './types/api';

type WorkspaceTab = 'documents' | 'contracts' | 'discovery' | 'review';

export default function App() {
  const [currentUser, setCurrentUser] = useState<UserProfile | null>(null);
  const [authChecking, setAuthChecking] = useState(true);

  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [matters, setMatters] = useState<Matter[]>([]);
  const [playbooks, setPlaybooks] = useState<Playbook[]>([]);
  const [selectedMatterId, setSelectedMatterId] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<WorkspaceTab>('documents');
  const [modalOpen, setModalOpen] = useState(false);
  const [teamModalOpen, setTeamModalOpen] = useState(false);
  const [playbookModalOpen, setPlaybookModalOpen] = useState(false);
  const [sourceTarget, setSourceTarget] = useState<SourceViewerTarget | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [findings, setFindings] = useState<ExtractedFinding[]>([]);
  const [tasks, setTasks] = useState<ReviewTaskItem[]>([]);
  const [auditLogs, setAuditLogs] = useState<AuditLogEntry[]>([]);

  useEffect(() => {
    async function restoreSession() {
      const existingToken = tokenStore.get();
      if (!existingToken) {
        setAuthChecking(false);
        return;
      }
      try {
        const user = await api.me();
        setCurrentUser(user);
      } catch {
        tokenStore.clear();
      } finally {
        setAuthChecking(false);
      }
    }
    void restoreSession();
  }, []);

  const loadBootstrap = useCallback(async () => {
    if (!currentUser) return;
    setLoading(true);
    try {
      const [h, mList, pList] = await Promise.all([
        api.health(),
        api.listMatters(),
        api.listPlaybooks(),
      ]);
      setHealth(h);
      setMatters(mList);
      setPlaybooks(pList);
      setSelectedMatterId((prev) => {
        if (prev && mList.some((m) => m.id === prev)) return prev;
        return mList[0]?.id ?? null;
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to connect to AI Paralegal API.');
    } finally {
      setLoading(false);
    }
  }, [currentUser]);

  const refreshMatterData = useCallback(async () => {
    if (!currentUser || !selectedMatterId) return;
    try {
      const [mList, docs, fList, tList, logs, pList] = await Promise.all([
        api.listMatters(),
        api.listDocuments(selectedMatterId),
        api.listFindings(selectedMatterId),
        api.listTasks(selectedMatterId),
        api.listAuditLogs(selectedMatterId),
        api.listPlaybooks(),
      ]);
      setMatters(mList);
      setDocuments(docs);
      setFindings(fList);
      setTasks(tList);
      setAuditLogs(logs);
      setPlaybooks(pList);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load matter workspace.');
    }
  }, [currentUser, selectedMatterId]);

  const { connected: sseConnected, liveActivity } = useMatterEvents(
    currentUser ? selectedMatterId : null,
    refreshMatterData
  );

  useEffect(() => {
    void loadBootstrap();
  }, [loadBootstrap]);

  useEffect(() => {
    void refreshMatterData();
  }, [refreshMatterData]);

  async function handleCreateMatter(payload: MatterCreateInput) {
    const created = await api.createMatter(payload);
    await loadBootstrap();
    setSelectedMatterId(created.id);
    setActiveTab('documents');
  }

  async function handleSignOut() {
    try {
      await api.logout(tokenStore.getRefresh());
    } catch {
      // ignore network/expired-token errors during logout
    }
    tokenStore.clear();
    setCurrentUser(null);
    setMatters([]);
    setSelectedMatterId(null);
    setSourceTarget(null);
    setDocuments([]);
    setFindings([]);
    setTasks([]);
    setAuditLogs([]);
  }

  function handleInspectCitation(citation: VerifiedCitation) {
    if (!citation.document_id) return;
    setSourceTarget({
      documentId: citation.document_id,
      filename: citation.document_filename,
      chunkId: citation.chunk_id,
      quote: citation.quote,
      pageNumber: citation.page_number,
      paragraphIndex: citation.paragraph_index,
      sectionHeading: citation.section_heading,
    });
  }

  function handleOpenDocument(doc: DocumentItem) {
    setSourceTarget({
      documentId: doc.id,
      filename: doc.filename,
    });
  }

  async function handleToggleLegalHold(matter: Matter) {
    setError(null);
    const nextState = !matter.legal_hold_active;
    try {
      await api.toggleLegalHold(
        matter.id,
        nextState,
        nextState ? 'Litigation hold activated via workspace' : 'Litigation hold released via workspace'
      );
      await refreshMatterData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to toggle litigation legal hold.');
    }
  }

  if (authChecking) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-900 text-xs text-slate-300 gap-2">
        <Loader2 className="h-4 w-4 animate-spin text-indigo-400" />
        Verifying session...
      </div>
    );
  }

  if (!currentUser) {
    return <AuthGate onAuthenticated={setCurrentUser} />;
  }

  const activeMatter = matters.find((m) => m.id === selectedMatterId) ?? null;

  return (
    <div className="flex h-screen overflow-hidden bg-slate-50">
      <MatterSidebar
        matters={matters}
        selectedMatterId={selectedMatterId}
        onSelectMatter={setSelectedMatterId}
        onNewMatter={() => setModalOpen(true)}
        onSignOut={() => void handleSignOut()}
        currentUser={currentUser}
        health={health}
      />

      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <DisclaimerBanner text={health?.disclaimer} />

        {loading ? (
          <div className="flex flex-1 items-center justify-center gap-2 text-sm text-slate-500">
            <Loader2 className="h-5 w-5 animate-spin text-indigo-600" />
            Connecting to AI Paralegal workspace...
          </div>
        ) : !activeMatter ? (
          <div className="flex flex-1 flex-col items-center justify-center p-8 text-center">
            <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-indigo-50 text-indigo-600">
              <Scale className="h-7 w-7" />
            </div>
            <h2 className="mt-4 text-lg font-semibold text-slate-900">Welcome to AI Paralegal</h2>
            <p className="mt-1.5 max-w-md text-xs leading-relaxed text-slate-500">
              {currentUser.role === 'auditor'
                ? 'No matters exist yet in the firm workspace.'
                : 'Create your first legal matter to ingest contracts and discovery records, run playbook-guided redlines, synthesize timelines, and review citation-grounded drafts.'}
            </p>
            {currentUser.role !== 'auditor' && (
              <button
                type="button"
                onClick={() => setModalOpen(true)}
                className="mt-5 rounded-lg bg-indigo-600 px-5 py-2.5 text-xs font-semibold text-white shadow-sm hover:bg-indigo-500"
              >
                + Create First Legal Matter
              </button>
            )}
          </div>
        ) : (
          <>
            {/* Matter Workspace Header */}
            <header className="border-b border-slate-200 bg-white px-6 pt-4">
              <div className="flex flex-wrap items-start justify-between gap-4 pb-4">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded bg-indigo-50 px-2 py-0.5 font-mono text-[11px] font-semibold text-indigo-700">
                      {activeMatter.matter_number}
                    </span>
                    <span className="rounded bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-700">
                      {activeMatter.practice_area}
                    </span>
                    {activeMatter.jurisdiction && (
                      <span className="rounded bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600">
                        {activeMatter.jurisdiction}
                      </span>
                    )}
                    <span
                      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                        sseConnected
                          ? 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200'
                          : 'bg-slate-100 text-slate-500'
                      }`}
                    >
                      <Activity className="h-3 w-3" />
                      {liveActivity ? liveActivity : sseConnected ? 'Live SSE' : 'Connecting SSE'}
                    </span>
                    {activeMatter.legal_hold_active && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-rose-100 px-2 py-0.5 text-[10px] font-bold uppercase text-rose-900 ring-1 ring-rose-300">
                        <Lock className="h-3 w-3" />
                        Legal Hold Active
                      </span>
                    )}
                  </div>
                  <h2 className="mt-1.5 text-lg font-bold text-slate-900">{activeMatter.title}</h2>
                  <p className="text-xs text-slate-500">Client: {activeMatter.client_name}</p>
                </div>

                <div className="flex flex-wrap items-center gap-2.5 text-xs">
                  {currentUser.role !== 'auditor' && (
                    <button
                      type="button"
                      data-testid="legal-hold-btn"
                      onClick={() => void handleToggleLegalHold(activeMatter)}
                      className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-semibold ${
                        activeMatter.legal_hold_active
                          ? 'border-rose-300 bg-rose-50 text-rose-900 hover:bg-rose-100'
                          : 'border-slate-300 bg-white text-slate-700 hover:bg-slate-50'
                      }`}
                    >
                      {activeMatter.legal_hold_active ? (
                        <Unlock className="h-3.5 w-3.5 text-rose-700" />
                      ) : (
                        <Lock className="h-3.5 w-3.5 text-slate-600" />
                      )}
                      {activeMatter.legal_hold_active ? 'Release Legal Hold' : 'Legal Hold'}
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => setTeamModalOpen(true)}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50"
                  >
                    <Users className="h-3.5 w-3.5 text-indigo-600" />
                    Team Access
                  </button>
                  <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5">
                    <span className="text-slate-500">Docs:</span>{' '}
                    <strong className="text-slate-900">{activeMatter.stats.document_count}</strong>
                  </div>
                  <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5">
                    <span className="text-amber-800">Pending Review:</span>{' '}
                    <strong className="text-amber-950">
                      {activeMatter.stats.pending_review_count}
                    </strong>
                  </div>
                  <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-1.5">
                    <span className="text-emerald-800">Approved:</span>{' '}
                    <strong className="text-emerald-950">
                      {activeMatter.stats.approved_count}
                    </strong>
                  </div>
                </div>
              </div>

              {/* Workspace Tabs */}
              <nav className="flex flex-wrap gap-1" aria-label="Matter workspace sections">
                {[
                  { id: 'documents', label: 'Documents & Provenance', icon: Files },
                  { id: 'contracts', label: 'Contract & Clause Risk', icon: ShieldAlert },
                  { id: 'discovery', label: 'Q&A, Synthesis & Drafting', icon: GitCompare },
                  { id: 'review', label: 'Review Queue & Audit Trail', icon: FileCheck2 },
                ].map((tab) => {
                  const Icon = tab.icon;
                  const active = activeTab === tab.id;
                  return (
                    <button
                      key={tab.id}
                      type="button"
                      onClick={() => setActiveTab(tab.id as WorkspaceTab)}
                      className={`inline-flex items-center gap-2 border-b-2 px-4 py-2.5 text-xs font-semibold transition ${
                        active
                          ? 'border-indigo-600 text-indigo-700'
                          : 'border-transparent text-slate-600 hover:border-slate-300 hover:text-slate-900'
                      }`}
                    >
                      <Icon className="h-4 w-4" />
                      {tab.label}
                    </button>
                  );
                })}
              </nav>
            </header>

            {error && (
              <div className="mx-6 mt-4 rounded-lg border border-rose-200 bg-rose-50 px-4 py-2.5 text-xs text-rose-800">
                {error}
              </div>
            )}

            <main className="flex-1 overflow-y-auto p-6">
              {activeTab === 'documents' && (
                <DocumentsTab
                  matterId={activeMatter.id}
                  documents={documents}
                  canWrite={currentUser.role !== 'auditor'}
                  legalHoldActive={Boolean(activeMatter.legal_hold_active)}
                  onOpenDocument={handleOpenDocument}
                  onRefresh={refreshMatterData}
                />
              )}
              {activeTab === 'contracts' && (
                <ContractReviewTab
                  matterId={activeMatter.id}
                  documents={documents}
                  findings={findings}
                  playbooks={playbooks}
                  currentUser={currentUser}
                  health={health}
                  onNewPlaybook={() => setPlaybookModalOpen(true)}
                  onInspectCitation={handleInspectCitation}
                  onRefresh={refreshMatterData}
                />
              )}
              {activeTab === 'discovery' && (
                <DiscoveryDraftingTab
                  matterId={activeMatter.id}
                  findings={findings}
                  tasks={tasks}
                  health={health}
                  onInspectCitation={handleInspectCitation}
                  onRefresh={refreshMatterData}
                />
              )}
              {activeTab === 'review' && (
                <ReviewAuditTab
                  matterId={activeMatter.id}
                  findings={findings}
                  auditLogs={auditLogs}
                  currentUser={currentUser}
                  onInspectCitation={handleInspectCitation}
                  onRefresh={refreshMatterData}
                />
              )}
            </main>
          </>
        )}
      </div>

      <NewMatterModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onCreate={handleCreateMatter}
      />

      {activeMatter && (
        <TeamAccessModal
          open={teamModalOpen}
          matterId={activeMatter.id}
          matterTitle={activeMatter.title}
          currentUser={currentUser}
          onClose={() => setTeamModalOpen(false)}
        />
      )}

      <PlaybookModal
        open={playbookModalOpen}
        onClose={() => setPlaybookModalOpen(false)}
        onCreated={(pb) => {
          setPlaybooks((prev) => [...prev, pb]);
        }}
      />

      <SourceViewerDrawer target={sourceTarget} onClose={() => setSourceTarget(null)} />
    </div>
  );
}
