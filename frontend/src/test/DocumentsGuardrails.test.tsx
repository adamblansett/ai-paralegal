import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { DocumentsTab } from '../components/DocumentsTab';
import type { DocumentItem } from '../types/api';

describe('DocumentsTab Guardrails & Bates Stamping', () => {
  it('renders privilege/PII/Bates indicators and invokes Bates stamp endpoint', async () => {
    const user = userEvent.setup();
    const onRefresh = vi.fn(async () => {});

    const docs: DocumentItem[] = [
      {
        id: 'doc-1',
        matter_id: 'm-1',
        filename: 'privileged_memo.txt',
        content_type: 'text/plain',
        document_type: 'memo',
        storage_backend: 'local',
        storage_key: 'k1',
        sha256_hash: 'abcdef123456',
        file_size_bytes: 1024,
        ingestion_status: 'indexed',
        error_message: null,
        page_count: 2,
        word_count: 140,
        chunk_count: 2,
        bates_start: 'PROD-000001',
        bates_end: 'PROD-000002',
        privilege_status: 'attorney_client',
        privilege_reason: 'Counsel advice',
        pii_detected_count: 3,
        pii_summary_json: { email: 2, ssn: 1 },
        metadata_json: {},
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
    ];

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        matter_id: 'm-1',
        prefix: 'ACME',
        stamped_count: 1,
        next_number: 3,
        documents: docs,
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<DocumentsTab matterId="m-1" documents={docs} canWrite={true} onRefresh={onRefresh} />);

    expect(screen.getByText('PROD-000001–PROD-000002')).toBeInTheDocument();
    expect(screen.getByText('3 PII')).toBeInTheDocument();
    expect(screen.getByText(/Privilege Log DOCX/i)).toBeInTheDocument();

    const prefixInput = screen.getByLabelText('Bates prefix');
    await user.clear(prefixInput);
    await user.type(prefixInput, 'ACME');
    await user.click(screen.getByRole('button', { name: /Apply Bates Stamps/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(onRefresh).toHaveBeenCalledTimes(1);

    vi.unstubAllGlobals();
  });
});
