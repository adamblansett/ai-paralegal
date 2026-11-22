import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { DocumentsTab } from '../components/DocumentsTab';
import { ReviewAuditTab } from '../components/ReviewAuditTab';
import type { DocumentItem, UserProfile } from '../types/api';

describe('Governance, Legal Hold, Audit Chain & DMS Import UI', () => {
  it('locks delete under legal hold and loads DMS vault files', async () => {
    const user = userEvent.setup();
    const docs: DocumentItem[] = [
      {
        id: 'd1',
        matter_id: 'm1',
        filename: 'held_agreement.txt',
        content_type: 'text/plain',
        document_type: 'contract',
        storage_backend: 'local',
        storage_key: 'k1',
        sha256_hash: 'deadbeef1234',
        file_size_bytes: 500,
        ingestion_status: 'indexed',
        error_message: null,
        page_count: 1,
        word_count: 50,
        chunk_count: 1,
        legal_hold_active: true,
        metadata_json: {},
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
    ];

    const fetchMock = vi.fn((url: string) => {
      if (url.includes('/integrations/dms/providers')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => [
            {
              provider: 'mock_dms',
              display_name: 'Built-In Legal Fixture Vault',
              configured: true,
              description: 'Vault',
            },
          ],
        });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => [
          {
            external_id: 'sample_msa.txt',
            name: 'sample_msa.txt',
            content_type: 'text/plain',
            document_type: 'contract',
            size_bytes: 2048,
            modified_at: new Date().toISOString(),
            source_uri: 'dms://mock_dms/samples/sample_msa.txt',
          },
        ],
      });
    });
    vi.stubGlobal('fetch', fetchMock);

    render(
      <DocumentsTab
        matterId="m1"
        documents={docs}
        canWrite={true}
        legalHoldActive={true}
        onRefresh={vi.fn(async () => {})}
      />
    );

    const deleteBtn = screen.getByTitle(/Deletion locked under active Litigation Legal Hold/i);
    expect(deleteBtn).toBeDisabled();

    await user.click(screen.getByRole('button', { name: /Import from DMS/i }));
    await waitFor(() => expect(screen.getByText('sample_msa.txt')).toBeInTheDocument());

    vi.unstubAllGlobals();
  });

  it('verifies SHA-256 audit log chain inside ReviewAuditTab', async () => {
    const user = userEvent.setup();
    const partnerUser: UserProfile = {
      id: 'u1',
      email: 'partner@firm.example.com',
      full_name: 'Elena Partner',
      role: 'partner',
      is_active: true,
      created_at: new Date().toISOString(),
    };

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        matter_id: 'm1',
        total_entries: 4,
        verified: true,
        broken_at_sequence: null,
        latest_hash: 'abcdef1234567890abcdef1234567890',
        detail: 'Verified intact SHA-256 hash chain.',
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    render(
      <ReviewAuditTab
        matterId="m1"
        findings={[]}
        auditLogs={[]}
        currentUser={partnerUser}
        onRefresh={vi.fn(async () => {})}
      />
    );

    await user.click(screen.getByRole('button', { name: /Immutable Audit Trail/i }));
    await user.click(screen.getByRole('button', { name: /Verify SHA-256 Audit Chain/i }));

    await waitFor(() => expect(screen.getByTestId('audit-chain-status')).toBeInTheDocument());
    expect(screen.getByTestId('audit-chain-status')).toHaveTextContent(/Chain Intact/i);

    vi.unstubAllGlobals();
  });
});
