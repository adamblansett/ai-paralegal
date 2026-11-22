import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ContractReviewTab } from '../components/ContractReviewTab';
import type { DocumentItem, ExtractedFinding, UserProfile } from '../types/api';

describe('ContractReviewTab Portfolio Risk Matrix', () => {
  it('switches to Portfolio Risk Matrix and filters findings when clicking a matrix cell', async () => {
    const user = userEvent.setup();

    const partnerUser: UserProfile = {
      id: 'u-1',
      email: 'partner@firm.example.com',
      full_name: 'Partner',
      role: 'partner',
      is_active: true,
      created_at: new Date().toISOString(),
    };

    const docs: DocumentItem[] = [
      {
        id: 'doc-1',
        matter_id: 'm-1',
        filename: 'vendor_msa.txt',
        content_type: 'text/plain',
        document_type: 'contract',
        storage_backend: 'local',
        storage_key: 'k1',
        sha256_hash: 'hash1',
        file_size_bytes: 512,
        ingestion_status: 'indexed',
        error_message: null,
        page_count: 1,
        word_count: 80,
        chunk_count: 1,
        metadata_json: {},
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
    ];

    const findings: ExtractedFinding[] = [
      {
        id: 'f-1',
        matter_id: 'm-1',
        document_id: 'doc-1',
        review_task_id: 't-1',
        finding_type: 'risk_flag',
        category: 'indemnification',
        title: 'Uncapped Client Indemnification',
        summary: 'Client indemnifies Vendor without cap.',
        edited_summary: null,
        severity: 'high',
        confidence: 0.9,
        grounding_score: 1.0,
        requires_escalation: true,
        escalation_reason: 'High risk',
        recommended_action: 'Negotiate mutual cap',
        citations_json: [],
        metadata_json: {},
        review_status: 'pending_review',
        reviewer_name: null,
        reviewer_notes: null,
        reviewed_at: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
    ];

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        matter_id: 'm-1',
        categories: ['indemnification', 'limitation_of_liability'],
        rows: [
          {
            document_id: 'doc-1',
            filename: 'vendor_msa.txt',
            bates_range: 'PROD-000001–PROD-000001',
            privilege_status: 'none',
            overall_risk: 'high',
            finding_count: 1,
            escalated_count: 1,
            cells: {
              indemnification: {
                severity: 'high',
                finding_count: 1,
                review_status: 'pending_review',
                escalated: true,
                finding_ids: ['f-1'],
                top_title: 'Uncapped Client Indemnification',
              },
            },
          },
        ],
        total_documents: 1,
        total_findings: 1,
        high_risk_documents: 1,
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    render(
      <ContractReviewTab
        matterId="m-1"
        documents={docs}
        findings={findings}
        playbooks={[]}
        currentUser={partnerUser}
        health={null}
        onNewPlaybook={vi.fn()}
        onRefresh={vi.fn(async () => {})}
      />
    );

    await user.click(screen.getByRole('button', { name: /Portfolio Risk Matrix/i }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /high \(1\)/i })).toBeInTheDocument()
    );

    await user.click(screen.getByRole('button', { name: /high \(1\)/i }));
    expect(screen.getByText('Uncapped Client Indemnification')).toBeInTheDocument();
    expect(screen.getByText(/Clear Matrix Filter/i)).toBeInTheDocument();

    vi.unstubAllGlobals();
  });
});
