import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { CitationBadge } from '../components/CitationBadge';
import { SourceViewerDrawer } from '../components/SourceViewerDrawer';
import type { VerifiedCitation } from '../types/api';

describe('CitationBadge & SourceViewerDrawer', () => {
  it('triggers onInspectCitation from View in Source and highlights matching quote in drawer', async () => {
    const user = userEvent.setup();
    const onInspect = vi.fn();

    const citation: VerifiedCitation = {
      chunk_id: 'chunk-2',
      chunk_ref: 'C2',
      document_id: 'doc-123',
      document_filename: 'sample_msa.txt',
      page_number: 2,
      paragraph_index: 5,
      section_heading: 'SECTION 7. LIMITATION OF LIABILITY',
      char_start: 100,
      char_end: 260,
      quote: 'ONE (1) MONTH OF FEES PAID',
      matched_excerpt: 'ONE (1) MONTH OF FEES PAID',
      verification_status: 'verified_exact',
      match_score: 1.0,
    };

    render(<CitationBadge citation={citation} index={0} onInspectCitation={onInspect} />);
    await user.click(screen.getByTestId('view-in-source-btn'));
    expect(onInspect).toHaveBeenCalledWith(citation);

    const fetchMock = vi.fn((url: string) => {
      if (url.endsWith('/documents/doc-123')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            id: 'doc-123',
            matter_id: 'm-1',
            filename: 'sample_msa.txt',
            content_type: 'text/plain',
            document_type: 'contract',
            storage_backend: 'local',
            storage_key: 'k',
            sha256_hash: 'abc123def456',
            file_size_bytes: 1200,
            ingestion_status: 'indexed',
            error_message: null,
            page_count: 2,
            word_count: 150,
            chunk_count: 2,
            bates_start: 'PROD-000001',
            bates_end: 'PROD-000002',
            privilege_status: 'confidential',
            pii_detected_count: 1,
            metadata_json: {},
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          }),
        });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => [
          {
            id: 'chunk-2',
            document_id: 'doc-123',
            matter_id: 'm-1',
            chunk_index: 1,
            content:
              'VENDOR TOTAL LIABILITY SHALL NOT EXCEED ONE (1) MONTH OF FEES PAID BY CLIENT.',
            page_number: 2,
            paragraph_index: 5,
            section_heading: 'SECTION 7. LIMITATION OF LIABILITY',
            char_start: 100,
            char_end: 260,
            token_count: 20,
            metadata_json: { bates_number: 'PROD-000002' },
            created_at: new Date().toISOString(),
          },
        ],
      });
    });
    vi.stubGlobal('fetch', fetchMock);

    render(
      <SourceViewerDrawer
        target={{
          documentId: 'doc-123',
          filename: 'sample_msa.txt',
          chunkId: 'chunk-2',
          quote: 'ONE (1) MONTH OF FEES PAID',
          pageNumber: 2,
        }}
        onClose={vi.fn()}
      />
    );

    await waitFor(() => expect(screen.getByTestId('citation-highlight')).toBeInTheDocument());
    expect(screen.getByTestId('citation-highlight')).toHaveTextContent('ONE (1) MONTH OF FEES PAID');
    expect(screen.getByText(/PROD-000001/)).toBeInTheDocument();

    vi.unstubAllGlobals();
  });
});
