import { useEffect, useState, type FormEvent } from 'react';
import {
  Bell,
  Building2,
  CheckCircle2,
  Loader2,
  Play,
  ShieldAlert,
  ShieldCheck,
  Trash2,
  X,
} from 'lucide-react';
import { api } from '../api/client';
import type {
  DMSProviderInfo,
  OrganizationInfo,
  RetentionSweepReport,
  UserProfile,
  UserRole,
  WebhookChannelType,
  WebhookSubscription,
} from '../types/api';

interface AdminIntegrationsModalProps {
  open: boolean;
  currentUser: UserProfile;
  matterId?: string | null;
  onClose: () => void;
  onRefresh?: () => Promise<void>;
}

type AdminTab = 'webhooks' | 'retention' | 'org_dms';

export function AdminIntegrationsModal({
  open,
  currentUser,
  matterId = null,
  onClose,
  onRefresh,
}: AdminIntegrationsModalProps) {
  const [tab, setTab] = useState<AdminTab>('webhooks');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);

  const [webhooks, setWebhooks] = useState<WebhookSubscription[]>([]);
  const [organization, setOrganization] = useState<OrganizationInfo | null>(null);
  const [users, setUsers] = useState<UserProfile[]>([]);
  const [dmsProviders, setDmsProviders] = useState<DMSProviderInfo[]>([]);
  const [sweepReport, setSweepReport] = useState<RetentionSweepReport | null>(null);

  // Webhook creation form
  const [whName, setWhName] = useState('');
  const [whChannel, setWhChannel] = useState<WebhookChannelType>('slack');
  const [whUrl, setWhUrl] = useState('');
  const [whSecret, setWhSecret] = useState('');
  const [creatingWebhook, setCreatingWebhook] = useState(false);

  // Retention editing
  const [retentionDaysInput, setRetentionDaysInput] = useState('2555');
  const [savingOrg, setSavingOrg] = useState(false);
  const [runningSweep, setRunningSweep] = useState(false);

  // TOTP MFA enrollment state
  const [mfaSecret, setMfaSecret] = useState<string | null>(null);
  const [mfaCode, setMfaCode] = useState('');
  const [mfaActive, setMfaActive] = useState(Boolean(currentUser.mfa_enabled));

  const isPartner = currentUser.role === 'partner';
  const canManageWebhooks = currentUser.role === 'partner' || currentUser.role === 'associate';

  async function loadAdminData() {
    setLoading(true);
    setError(null);
    try {
      const [whList, orgData, userList, dmsList] = await Promise.all([
        api.listWebhooks(),
        api.getOrganization(),
        api.listUsers(),
        api.listDMSProviders(),
      ]);
      setWebhooks(whList);
      setOrganization(orgData);
      setRetentionDaysInput(String(orgData.retention_days));
      setUsers(userList);
      setDmsProviders(dmsList);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load admin settings.');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!open) return;
    setStatusMessage(null);
    setSweepReport(null);
    void loadAdminData();
  }, [open]);

  if (!open) return null;

  async function handleCreateWebhook(e: FormEvent) {
    e.preventDefault();
    if (!whName.trim() || !whUrl.trim()) return;
    setCreatingWebhook(true);
    setError(null);
    setStatusMessage(null);
    try {
      const created = await api.createWebhook({
        name: whName.trim(),
        channel_type: whChannel,
        target_url: whUrl.trim(),
        signing_secret: whSecret.trim() || undefined,
        matter_id: matterId ?? undefined,
      });
      setWebhooks((prev) => [created, ...prev]);
      setWhName('');
      setWhUrl('');
      setWhSecret('');
      setStatusMessage(`Webhook "${created.name}" registered.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to register webhook.');
    } finally {
      setCreatingWebhook(false);
    }
  }

  async function handleToggleWebhook(wh: WebhookSubscription) {
    setError(null);
    setStatusMessage(null);
    try {
      const updated = await api.updateWebhook(wh.id, { is_active: !wh.is_active });
      setWebhooks((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
      setStatusMessage(
        `Webhook "${updated.name}" is now ${updated.is_active ? 'active' : 'paused'}.`
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update webhook status.');
    }
  }

  async function handleTestWebhook(wh: WebhookSubscription) {
    setError(null);
    setStatusMessage(null);
    try {
      const result = await api.testWebhook(wh.id);
      if (result.delivered) {
        setStatusMessage(
          `Test alert delivered to "${wh.name}" (HTTP ${result.status_code ?? 200}).`
        );
      } else {
        setError(
          `Test delivery to "${wh.name}" failed: ${result.error ?? 'Unknown delivery error'}.`
        );
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Webhook test failed.');
    }
  }

  async function handleDeleteWebhook(wh: WebhookSubscription) {
    setError(null);
    setStatusMessage(null);
    try {
      await api.deleteWebhook(wh.id);
      setWebhooks((prev) => prev.filter((item) => item.id !== wh.id));
      setStatusMessage(`Webhook "${wh.name}" deleted.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete webhook.');
    }
  }

  async function handleSaveRetentionDays(e: FormEvent) {
    e.preventDefault();
    const parsedDays = Number.parseInt(retentionDaysInput, 10);
    if (!Number.isFinite(parsedDays) || parsedDays < 1) {
      setError('Retention days must be a positive integer.');
      return;
    }
    setSavingOrg(true);
    setError(null);
    setStatusMessage(null);
    try {
      const updated = await api.updateOrganization({ retention_days: parsedDays });
      setOrganization(updated);
      setStatusMessage(`Default tenant retention policy set to ${updated.retention_days} days.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update retention window.');
    } finally {
      setSavingOrg(false);
    }
  }

  async function handleRunSweep(dryRun: boolean) {
    setRunningSweep(true);
    setError(null);
    setStatusMessage(null);
    try {
      const report = await api.runRetentionSweep({ dry_run: dryRun });
      setSweepReport(report);
      if (!dryRun && report.purged_count > 0 && onRefresh) {
        await onRefresh();
      }
      setStatusMessage(
        dryRun
          ? `Dry run evaluated ${report.evaluated_count} document(s): ${report.eligible_count} eligible, ${report.skipped_legal_hold_count} protected by Legal Hold.`
          : `Purge sweep completed: ${report.purged_count} purged, ${report.skipped_legal_hold_count} skipped due to Legal Hold.`
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Retention sweep failed.');
    } finally {
      setRunningSweep(false);
    }
  }

  async function handleRoleChange(user: UserProfile, role: UserRole) {
    setError(null);
    setStatusMessage(null);
    try {
      const updated = await api.updateAdminUser(user.id, { role });
      setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)));
      setStatusMessage(`Updated ${updated.full_name} to role "${updated.role}".`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to update user role.');
    }
  }

  async function handleEnrollMfa() {
    setError(null);
    setStatusMessage(null);
    try {
      const res = await api.enrollMfa();
      setMfaSecret(res.secret);
      setStatusMessage('TOTP secret generated. Enter a 6-digit authenticator code to activate.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'MFA enrollment failed.');
    }
  }

  async function handleConfirmMfa() {
    if (!mfaCode.trim()) return;
    setError(null);
    setStatusMessage(null);
    try {
      const updated = mfaActive
        ? await api.disableMfa(mfaCode.trim())
        : await api.verifyMfa(mfaCode.trim());
      setMfaActive(Boolean(updated.mfa_enabled));
      setMfaSecret(null);
      setMfaCode('');
      setStatusMessage(
        updated.mfa_enabled ? 'TOTP MFA is now enabled on your account.' : 'TOTP MFA disabled.'
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Invalid TOTP verification code.');
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Admin and Integrations Console"
    >
      <div className="flex max-h-[88vh] w-full max-w-3xl flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
          <div className="flex items-center gap-2.5">
            <ShieldCheck className="h-5 w-5 text-indigo-600" />
            <div>
              <h3 className="text-sm font-bold text-slate-900">
                Enterprise Admin & Integrations
              </h3>
              <p className="text-xs text-slate-500">
                {organization ? `${organization.name} (${organization.slug})` : 'Tenant Controls'}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close Admin Console"
            className="rounded-lg p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex gap-1 border-b border-slate-200 bg-slate-50 px-5">
          {[
            { id: 'webhooks', label: 'Webhooks & Alerts', icon: Bell },
            { id: 'retention', label: 'Retention & Compliance', icon: ShieldAlert },
            { id: 'org_dms', label: 'Org & DMS Status', icon: Building2 },
          ].map((item) => {
            const Icon = item.icon;
            const active = tab === item.id;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => setTab(item.id as AdminTab)}
                className={`inline-flex items-center gap-1.5 border-b-2 px-3 py-2.5 text-xs font-semibold ${
                  active
                    ? 'border-indigo-600 text-indigo-700'
                    : 'border-transparent text-slate-600 hover:text-slate-900'
                }`}
              >
                <Icon className="h-3.5 w-3.5" />
                {item.label}
              </button>
            );
          })}
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto p-5 text-xs">
          {error && (
            <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-rose-800">
              {error}
            </div>
          )}
          {statusMessage && (
            <div className="flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-emerald-800">
              <CheckCircle2 className="h-4 w-4 shrink-0" />
              <span>{statusMessage}</span>
            </div>
          )}

          {loading ? (
            <div className="flex items-center justify-center gap-2 py-8 text-slate-500">
              <Loader2 className="h-4 w-4 animate-spin text-indigo-600" />
              Loading enterprise settings...
            </div>
          ) : tab === 'webhooks' ? (
            <div className="space-y-4">
              {canManageWebhooks && (
                <form
                  onSubmit={(e) => void handleCreateWebhook(e)}
                  className="grid grid-cols-1 gap-3 rounded-lg border border-slate-200 bg-slate-50 p-3.5 sm:grid-cols-2"
                >
                  <div>
                    <label className="mb-1 block font-semibold text-slate-700">
                      Subscription Name
                    </label>
                    <input
                      type="text"
                      required
                      value={whName}
                      onChange={(e) => setWhName(e.target.value)}
                      placeholder="Partner Escalations Slack"
                      className="w-full rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block font-semibold text-slate-700">Channel Type</label>
                    <select
                      value={whChannel}
                      onChange={(e) => setWhChannel(e.target.value as WebhookChannelType)}
                      className="w-full rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                    >
                      <option value="slack">Slack</option>
                      <option value="teams">Microsoft Teams</option>
                      <option value="webhook">HTTPS Webhook (HMAC-SHA256)</option>
                    </select>
                  </div>
                  <div className="sm:col-span-2">
                    <label className="mb-1 block font-semibold text-slate-700">Target URL</label>
                    <input
                      type="url"
                      required
                      value={whUrl}
                      onChange={(e) => setWhUrl(e.target.value)}
                      placeholder="https://hooks.slack.com/services/..."
                      className="w-full rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block font-semibold text-slate-700">
                      HMAC Signing Secret (optional)
                    </label>
                    <input
                      type="password"
                      value={whSecret}
                      onChange={(e) => setWhSecret(e.target.value)}
                      placeholder="32+ char shared secret"
                      className="w-full rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
                    />
                  </div>
                  <div className="flex items-end justify-end">
                    <button
                      type="submit"
                      disabled={creatingWebhook}
                      className="rounded-lg bg-indigo-600 px-3.5 py-1.5 font-semibold text-white hover:bg-indigo-500 disabled:opacity-60"
                    >
                      {creatingWebhook ? 'Saving...' : 'Add Webhook'}
                    </button>
                  </div>
                </form>
              )}

              <div className="space-y-2">
                <h4 className="font-semibold text-slate-900">
                  Configured Subscriptions ({webhooks.length})
                </h4>
                {webhooks.length === 0 ? (
                  <p className="rounded-lg border border-dashed border-slate-300 p-4 text-center text-slate-500">
                    No outbound webhooks configured yet.
                  </p>
                ) : (
                  webhooks.map((wh) => (
                    <div
                      key={wh.id}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200 p-3"
                    >
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-slate-900">{wh.name}</span>
                          <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-slate-700">
                            {wh.channel_type}
                          </span>
                          <span
                            className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                              wh.is_active
                                ? 'bg-emerald-50 text-emerald-700'
                                : 'bg-amber-50 text-amber-800'
                            }`}
                          >
                            {wh.is_active ? 'Active' : 'Paused'}
                          </span>
                        </div>
                        <p className="mt-0.5 font-mono text-[11px] text-slate-500">
                          {wh.target_url}
                        </p>
                      </div>
                      {canManageWebhooks && (
                        <div className="flex items-center gap-1.5">
                          <button
                            type="button"
                            onClick={() => void handleTestWebhook(wh)}
                            className="inline-flex items-center gap-1 rounded border border-slate-300 px-2.5 py-1 font-medium text-slate-700 hover:bg-slate-50"
                          >
                            <Play className="h-3 w-3" />
                            Send Test
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleToggleWebhook(wh)}
                            className="rounded border border-slate-300 px-2.5 py-1 font-medium text-slate-700 hover:bg-slate-50"
                          >
                            {wh.is_active ? 'Pause' : 'Resume'}
                          </button>
                          <button
                            type="button"
                            aria-label={`Delete ${wh.name}`}
                            onClick={() => void handleDeleteWebhook(wh)}
                            className="rounded border border-rose-200 p-1 text-rose-600 hover:bg-rose-50"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      )}
                    </div>
                  ))
                )}
              </div>
            </div>
          ) : tab === 'retention' ? (
            <div className="space-y-4">
              <div className="rounded-lg border border-slate-200 bg-slate-50 p-3.5">
                <h4 className="font-semibold text-slate-900">
                  Tenant Default Retention Policy
                </h4>
                <p className="mt-0.5 text-slate-600">
                  Documents past their explicit retention date or tenant default age are eligible for
                  scheduled purge unless protected by an active Litigation Legal Hold.
                </p>
                <form
                  onSubmit={(e) => void handleSaveRetentionDays(e)}
                  className="mt-3 flex flex-wrap items-center gap-2"
                >
                  <label className="font-medium text-slate-700" htmlFor="retention-days-input">
                    Retention window (days):
                  </label>
                  <input
                    id="retention-days-input"
                    type="number"
                    min={1}
                    max={36500}
                    disabled={!isPartner}
                    value={retentionDaysInput}
                    onChange={(e) => setRetentionDaysInput(e.target.value)}
                    className="w-28 rounded-md border border-slate-300 bg-white px-2.5 py-1 text-xs disabled:bg-slate-100"
                  />
                  {isPartner && (
                    <button
                      type="submit"
                      disabled={savingOrg}
                      className="rounded-md bg-indigo-600 px-3 py-1 font-semibold text-white hover:bg-indigo-500 disabled:opacity-60"
                    >
                      {savingOrg ? 'Saving...' : 'Save Policy'}
                    </button>
                  )}
                </form>
              </div>

              <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200 p-3.5">
                <div>
                  <h4 className="font-semibold text-slate-900">On-Demand Compliance Sweep</h4>
                  <p className="text-slate-500">
                    Preview eligible expirations or execute purge across expired non-held files.
                  </p>
                </div>
                {isPartner ? (
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      disabled={runningSweep}
                      onClick={() => void handleRunSweep(true)}
                      className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-60"
                    >
                      Preview Expired (Dry Run)
                    </button>
                    <button
                      type="button"
                      disabled={runningSweep}
                      onClick={() => void handleRunSweep(false)}
                      className="rounded-lg bg-rose-600 px-3 py-1.5 font-semibold text-white hover:bg-rose-500 disabled:opacity-60"
                    >
                      Execute Purge
                    </button>
                  </div>
                ) : (
                  <span className="text-slate-500">Partner role required to execute sweeps.</span>
                )}
              </div>

              {sweepReport && (
                <div className="space-y-2 rounded-lg border border-slate-200 p-3.5">
                  <div className="flex flex-wrap gap-3 font-semibold">
                    <span>Evaluated: {sweepReport.evaluated_count}</span>
                    <span className="text-amber-700">Eligible: {sweepReport.eligible_count}</span>
                    <span className="text-rose-700">Purged: {sweepReport.purged_count}</span>
                    <span className="text-indigo-700">
                      Protected by Legal Hold: {sweepReport.skipped_legal_hold_count}
                    </span>
                  </div>
                  {sweepReport.items.length > 0 && (
                    <ul className="divide-y divide-slate-100">
                      {sweepReport.items.map((item) => (
                        <li
                          key={item.document_id}
                          className="flex items-center justify-between py-1.5"
                        >
                          <span className="font-medium text-slate-800">{item.filename}</span>
                          <span className="rounded bg-slate-100 px-2 py-0.5 text-[11px] text-slate-700">
                            {item.action}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-4">
              {organization && (
                <div className="grid grid-cols-3 gap-3">
                  <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                    <div className="text-slate-500">Firm Users</div>
                    <div className="mt-1 text-base font-bold text-slate-900">
                      {organization.user_count}
                    </div>
                  </div>
                  <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                    <div className="text-slate-500">Tenant Matters</div>
                    <div className="mt-1 text-base font-bold text-slate-900">
                      {organization.matter_count}
                    </div>
                  </div>
                  <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                    <div className="text-slate-500">Webhooks</div>
                    <div className="mt-1 text-base font-bold text-slate-900">
                      {organization.webhook_count}
                    </div>
                  </div>
                </div>
              )}

              <div className="rounded-lg border border-slate-200 p-3.5 space-y-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <h4 className="font-semibold text-slate-900">
                      Multi-Factor Authentication (TOTP MFA)
                    </h4>
                    <p className="text-slate-500">
                      Status: <strong>{mfaActive ? 'Enabled' : 'Not Enabled'}</strong>
                    </p>
                  </div>
                  {!mfaActive && !mfaSecret && (
                    <button
                      type="button"
                      onClick={() => void handleEnrollMfa()}
                      className="rounded-md bg-indigo-600 px-3 py-1 font-semibold text-white hover:bg-indigo-500"
                    >
                      Enable TOTP MFA
                    </button>
                  )}
                </div>
                {mfaSecret && (
                  <p className="rounded bg-slate-50 px-2.5 py-1.5 font-mono text-[11px] text-slate-800">
                    Authenticator Secret: {mfaSecret}
                  </p>
                )}
                {(mfaSecret || mfaActive) && (
                  <div className="flex items-center gap-2">
                    <input
                      type="text"
                      value={mfaCode}
                      onChange={(e) => setMfaCode(e.target.value)}
                      placeholder="6-digit TOTP code"
                      className="w-36 rounded border border-slate-300 px-2.5 py-1 text-xs"
                    />
                    <button
                      type="button"
                      onClick={() => void handleConfirmMfa()}
                      className="rounded border border-slate-300 bg-white px-3 py-1 font-semibold text-slate-700 hover:bg-slate-50"
                    >
                      {mfaActive ? 'Disable MFA' : 'Verify & Activate'}
                    </button>
                  </div>
                )}
              </div>

              <div>
                <h4 className="mb-2 font-semibold text-slate-900">Tenant Directory & Roles</h4>
                <div className="divide-y divide-slate-100 rounded-lg border border-slate-200">
                  {users.map((u) => (
                    <div
                      key={u.id}
                      className="flex flex-wrap items-center justify-between gap-2 px-3 py-2"
                    >
                      <div>
                        <span className="font-semibold text-slate-900">{u.full_name}</span>{' '}
                        <span className="text-slate-500">({u.email})</span>
                      </div>
                      {isPartner ? (
                        <select
                          aria-label={`Role for ${u.email}`}
                          value={u.role}
                          onChange={(e) => void handleRoleChange(u, e.target.value as UserRole)}
                          className="rounded border border-slate-300 bg-white px-2 py-1 text-xs"
                        >
                          <option value="partner">partner</option>
                          <option value="associate">associate</option>
                          <option value="paralegal">paralegal</option>
                          <option value="auditor">auditor</option>
                        </select>
                      ) : (
                        <span className="rounded bg-slate-100 px-2 py-0.5 text-[11px] font-semibold text-slate-700">
                          {u.role}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              </div>

              <div>
                <h4 className="mb-2 font-semibold text-slate-900">DMS Connector Readiness</h4>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  {dmsProviders.map((prov) => (
                    <div
                      key={prov.provider}
                      className="rounded-lg border border-slate-200 p-2.5"
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-semibold text-slate-900">{prov.display_name}</span>
                        <span
                          className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                            prov.configured
                              ? 'bg-emerald-50 text-emerald-700'
                              : 'bg-slate-100 text-slate-600'
                          }`}
                        >
                          {prov.configured ? 'Configured' : 'Env Setup Required'}
                        </span>
                      </div>
                      <p className="mt-1 text-[11px] text-slate-500">{prov.description}</p>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
