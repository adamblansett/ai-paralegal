import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../api/client';
import { ContractReviewTab } from '../components/ContractReviewTab';
import { DiscoveryDraftingTab } from '../components/DiscoveryDraftingTab';
import { NewMatterModal } from '../components/NewMatterModal';
import type { DocumentItem, UserProfile } from '../types/api';

const partnerUser: UserProfile = {
  id: 'u1',
  organization_id: 'org1',
  email: 'partner@firm.example.com',
  full_name: 'Alex Morgan',
  role: 'partner',
  is_active: true,
  created_at: new Date().toISOString(),
};

const indexedDocs: DocumentItem[] = [
  {
    id: 'doc-v1',
    matter_id: 'm1',
    filename: 'msa_v1.txt',
    content_type: 'text/plain',
    document_type: 'contract',
    storage_backend: 'local',
    storage_key: 'matters/m1/msa_v1.txt',
    sha256_hash: 'aaa',
    file_size_bytes: 200,
    ingestion_status: 'indexed',
    error_message: null,
    page_count: 1,
    word_count: 45,
    chunk_count: 2,
    bates_start: null,
    bates_end: null,
    privilege_status: 'none',
    privilege_reason: null,
    pii_detected_count: 0,
    pii_summary_json: {},
    legal_hold_active: false,
    retention_until: null,
    metadata_json: {},
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  },
  {
    id: 'doc-v2',
    matter_id: 'm1',
    filename: 'msa_v2.txt',
    content_type: 'text/plain',
    document_type: 'contract',
    storage_backend: 'local',
    storage_key: 'matters/m1/msa_v2.txt',
    sha256_hash: 'bbb',
    file_size_bytes: 220,
    ingestion_status: 'indexed',
    error_message: null,
    page_count: 1,
    word_count: 50,
    chunk_count: 2,
    bates_start: null,
    bates_end: null,
    privilege_status: 'none',
    privilege_reason: null,
    pii_detected_count: 0,
    pii_summary_json: {},
    legal_hold_active: false,
    retention_until: null,
    metadata_json: {},
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  },
];

describe('Legal-Ops Workflows & Streaming UI', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('screens intake conflicts in NewMatterModal', async () => {
    const conflictSpy = vi.spyOn(api, 'runConflictCheck').mockResolvedValue({
      query_client: 'Buyer LLC',
      query_counterparties: ['Apex Cloud'],
      has_conflicts: true,
      risk_level: 'high',
      recommended_action: 'Establish an Ethical Wall before staffing.',
      matches: [
        {
          matter_id: 'm0',
          matter_number: 'MAT-001',
          matter_title: 'Apex Prior Matter',
          client_name: 'Apex Cloud Inc.',
          matched_party: 'Apex Cloud',
          match_source: 'client_name',
          confidence: 0.95,
          severity: 'high',
          snippet: 'Existing matter client',
        },
      ],
    });

    render(<NewMatterModal open={true} onClose={vi.fn()} onCreate={vi.fn()} />);

    fireEvent.change(screen.getByPlaceholderText(/Meridian Legal Group LLP/i), {
      target: { value: 'Buyer LLC' },
    });
    fireEvent.change(screen.getByPlaceholderText(/Apex Cloud Systems/i), {
      target: { value: 'Apex Cloud' },
    });
    fireEvent.click(screen.getByTestId('conflict-check-btn'));

    await waitFor(() => {
      expect(conflictSpy).toHaveBeenCalled();
    });
    expect(await screen.findByText(/Conflict Screen: HIGH/i)).toBeInTheDocument();
  });

  it('compares two contract versions and displays risk regressions in ContractReviewTab', async () => {
    const compareSpy = vi.spyOn(api, 'compareDocuments').mockResolvedValue({
      matter_id: 'm1',
      base_document_id: 'doc-v1',
      base_filename: 'msa_v1.txt',
      revised_document_id: 'doc-v2',
      revised_filename: 'msa_v2.txt',
      total_hunks: 1,
      added_count: 0,
      removed_count: 0,
      modified_count: 1,
      unchanged_count: 0,
      hunks: [
        {
          section_heading: 'Liability',
          change_type: 'modified',
          base_text: '$500,000',
          revised_text: '$50,000',
          unified_diff: '--- v1\n+++ v2\n-$500,000\n+$50,000',
          risk_impact: 'Modified',
        },
      ],
      risk_regressions: [
        {
          severity: 'high',
          category: 'limitation_of_liability',
          title: 'Liability cap reduced in revised version',
          detail: 'Reduced from $500,000 to $50,000.',
          base_excerpt: null,
          revised_excerpt: null,
        },
      ],
      summary_markdown: '# Diff',
    });

    render(
      <ContractReviewTab
        matterId="m1"
        documents={indexedDocs}
        findings={[]}
        playbooks={[]}
        currentUser={partnerUser}
        health={null}
        onNewPlaybook={vi.fn()}
        onRefresh={vi.fn()}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: /Version Diff/i }));
    fireEvent.change(screen.getByLabelText('Base contract version'), {
      target: { value: 'doc-v1' },
    });
    fireEvent.change(screen.getByLabelText('Revised contract version'), {
      target: { value: 'doc-v2' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Compare Versions/i }));

    await waitFor(() => {
      expect(compareSpy).toHaveBeenCalledWith('m1', 'doc-v1', 'doc-v2');
    });
    expect(await screen.findByText(/Liability cap reduced in revised version/i)).toBeInTheDocument();
  });

  it('streams research Q&A and extracts obligations in DiscoveryDraftingTab', async () => {
    const streamSpy = vi
      .spyOn(api, 'researchQueryStream')
      .mockImplementation(async (_mId, _payload, callbacks) => {
        callbacks?.onToken?.('Liability ');
        callbacks?.onToken?.('is capped.');
        return {
          task_id: 't1',
          finding_id: null,
          question: 'What is the cap?',
          answer: 'Liability is capped.',
          key_points: [],
          uncertainties: [],
          missing_context: [],
          confidence: 0.9,
          grounding_score: 1.0,
          requires_escalation: false,
          escalation_reason: null,
          citations: [],
          provider: 'mock',
          model_name: 'mock-legal-deterministic-v1',
          disclaimer: 'Disclaimer',
        };
      });

    vi.spyOn(api, 'listObligations').mockResolvedValue([]);
    const extractSpy = vi.spyOn(api, 'extractObligations').mockResolvedValue([
      {
        id: 'ob1',
        matter_id: 'm1',
        document_id: 'doc-v1',
        title: 'msa_v1.txt: Renewal notice deadline (90 days prior)',
        category: 'renewal_notice',
        due_date: '2027-01-01T00:00:00Z',
        notice_window_days: 90,
        owner_name: null,
        status: 'open',
        source_quote: '90 days notice',
        notes: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
    ]);

    render(
      <DiscoveryDraftingTab
        matterId="m1"
        findings={[]}
        tasks={[]}
        health={null}
        onRefresh={vi.fn()}
      />
    );

    fireEvent.change(screen.getByPlaceholderText(/What annual fee/i), {
      target: { value: 'What is the cap?' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Answer with Citations/i }));

    await waitFor(() => {
      expect(streamSpy).toHaveBeenCalled();
    });
    expect(await screen.findByText('Liability is capped.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Obligations & Deadlines/i }));
    fireEvent.click(await screen.findByRole('button', { name: /Extract Deadlines/i }));

    await waitFor(() => {
      expect(extractSpy).toHaveBeenCalledWith('m1');
    });
  });
});
