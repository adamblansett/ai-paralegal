import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../api/client';
import { AdminIntegrationsModal } from '../components/AdminIntegrationsModal';
import type { UserProfile } from '../types/api';

const partnerUser: UserProfile = {
  id: 'u1',
  organization_id: 'org1',
  email: 'partner@firm.example.com',
  full_name: 'Alex Morgan',
  role: 'partner',
  is_active: true,
  created_at: new Date().toISOString(),
};

describe('AdminIntegrationsModal', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, 'listWebhooks').mockResolvedValue([
      {
        id: 'wh1',
        organization_id: 'org1',
        matter_id: null,
        name: 'Existing Slack Escalations',
        channel_type: 'slack',
        target_url: 'https://hooks.slack.example.com/services/T/B/X',
        events_json: ['finding.escalated'],
        is_active: true,
        created_at: new Date().toISOString(),
      },
    ]);
    vi.spyOn(api, 'getOrganization').mockResolvedValue({
      id: 'org1',
      slug: 'default-firm',
      name: 'Meridian Legal Group LLP',
      retention_days: 2555,
      is_active: true,
      created_at: new Date().toISOString(),
      user_count: 4,
      matter_count: 2,
      webhook_count: 1,
    });
    vi.spyOn(api, 'listUsers').mockResolvedValue([partnerUser]);
    vi.spyOn(api, 'listDMSProviders').mockResolvedValue([
      {
        provider: 'mock_dms',
        display_name: 'Sample Legal Vault (Mock DMS)',
        configured: true,
        description: 'Built-in sample legal vault.',
      },
    ]);
  });

  it('manages webhooks, triggers test delivery, runs retention dry-run, and displays DMS status', async () => {
    const testSpy = vi.spyOn(api, 'testWebhook').mockResolvedValue({
      delivered: true,
      status_code: 200,
      error: null,
    });
    const updateWhSpy = vi.spyOn(api, 'updateWebhook').mockResolvedValue({
      id: 'wh1',
      organization_id: 'org1',
      matter_id: null,
      name: 'Existing Slack Escalations',
      channel_type: 'slack',
      target_url: 'https://hooks.slack.example.com/services/T/B/X',
      events_json: ['finding.escalated'],
      is_active: false,
      created_at: new Date().toISOString(),
    });
    const sweepSpy = vi.spyOn(api, 'runRetentionSweep').mockResolvedValue({
      organization_id: 'org1',
      dry_run: true,
      as_of: new Date().toISOString(),
      evaluated_count: 3,
      purged_count: 0,
      eligible_count: 1,
      skipped_legal_hold_count: 1,
      items: [
        {
          document_id: 'd1',
          matter_id: 'm1',
          filename: 'old_msa.pdf',
          action: 'eligible_for_purge',
          reason: 'Expired',
          retention_until: '2020-01-01T00:00:00Z',
        },
        {
          document_id: 'd2',
          matter_id: 'm2',
          filename: 'held_evidence.txt',
          action: 'skipped_legal_hold',
          reason: 'Legal Hold active',
          retention_until: '2020-01-01T00:00:00Z',
        },
      ],
    });

    render(
      <AdminIntegrationsModal
        open={true}
        currentUser={partnerUser}
        matterId="m1"
        onClose={vi.fn()}
      />
    );

    expect(await screen.findByText('Existing Slack Escalations')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Send Test/i }));
    await waitFor(() => {
      expect(testSpy).toHaveBeenCalledWith('wh1');
    });
    expect(await screen.findByText(/Test alert delivered to "Existing Slack Escalations"/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
    await waitFor(() => {
      expect(updateWhSpy).toHaveBeenCalledWith('wh1', { is_active: false });
    });

    fireEvent.click(screen.getByRole('button', { name: /Retention & Compliance/i }));
    fireEvent.click(await screen.findByRole('button', { name: /Preview Expired \(Dry Run\)/i }));
    await waitFor(() => {
      expect(sweepSpy).toHaveBeenCalledWith({ dry_run: true });
    });
    expect(await screen.findByText(/Protected by Legal Hold: 1/i)).toBeInTheDocument();
    expect(screen.getByText('held_evidence.txt')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Org & DMS Status/i }));
    expect(await screen.findByText('Sample Legal Vault (Mock DMS)')).toBeInTheDocument();
  });
});
