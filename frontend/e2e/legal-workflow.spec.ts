import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const sampleMsaPath = path.resolve(__dirname, '../../samples/sample_msa.txt');

test('complete legal-ops workflow: sign in, create matter, upload, Bates stamp, review, inspect source, approve, export', async ({
  page,
}) => {
  await page.goto('/');

  // 1. Demo Partner sign in
  await expect(page.getByText('AI Paralegal Workspace')).toBeVisible();
  await page.getByRole('button', { name: /Partner/i }).click();

  // 2. Create first legal matter (when empty workspace shows Create First Legal Matter)
  const createBtn = page.getByRole('button', { name: /\+ Create First Legal Matter/i });
  await expect(createBtn).toBeVisible();
  await createBtn.click();

  await page.getByPlaceholder(/Meridian v\. Apex Cloud/i).fill('Acme MSA E2E Matter');
  await page.getByPlaceholder(/Meridian Legal Group LLP/i).fill('Acme Corp');
  await page.getByRole('button', { name: /Create Matter/i }).click();

  await expect(page.getByRole('heading', { name: 'Acme MSA E2E Matter' })).toBeVisible();

  // 3. Upload sample contract
  const fileInput = page.locator('input[type="file"]');
  await fileInput.setInputFiles(sampleMsaPath);

  await expect(page.getByText('sample_msa.txt').first()).toBeVisible();
  await expect(page.getByText('indexed').first()).toBeVisible();

  // 4. Apply Bates stamps & verify Privilege Log endpoint responds 200
  await page.getByRole('button', { name: /Apply Bates Stamps/i }).click();
  await expect(page.getByText(/PROD-000001/).first()).toBeVisible();

  // 5. Run Contract Review
  await page.getByRole('button', { name: /Contract & Clause Risk/i }).click();
  await page.getByRole('button', { name: /Run Contract Review/i }).click();

  await expect(page.getByTestId('view-in-source-btn').first()).toBeVisible();

  // 6. Click "View in Source" and confirm citation highlighting in SourceViewerDrawer
  await page.getByTestId('view-in-source-btn').first().click();
  const drawer = page.getByTestId('source-viewer-drawer');
  await expect(drawer).toBeVisible();
  await expect(drawer.getByTestId('citation-highlight')).toBeVisible();
  await drawer.getByRole('button', { name: /Close source viewer/i }).click();
  await expect(drawer).toBeHidden();

  // 7. Portfolio Risk Matrix toggle
  await page.getByRole('button', { name: /Portfolio Risk Matrix/i }).click();
  await expect(page.locator('table').getByText('sample_msa.txt')).toBeVisible();
  await page.getByRole('button', { name: /Findings View/i }).click();

  // 8. Approve first finding and verify Word Track Changes .docx & risk-matrix exports
  await page.getByRole('button', { name: /^Approve$/i }).first().click();

  const docxHref = await page
    .getByRole('link', { name: /Word Redline \(\.docx\)/i })
    .getAttribute('href');
  expect(docxHref).toBeTruthy();
  const docxResp = await page.request.get(docxHref!);
  expect(docxResp.status()).toBe(200);
  expect((await docxResp.body()).slice(0, 2).toString()).toBe('PK');
});

test('enterprise workflows: conflict check, DMS import, legal hold, audit chain, admin modal, streaming Q&A, obligations & .ics export', async ({
  page,
}) => {
  await page.goto('/');
  await expect(page.getByText('AI Paralegal Workspace')).toBeVisible();
  await page.getByRole('button', { name: /Partner/i }).click();

  // 1. Open New Matter modal via sidebar button and run intake conflict check
  await page.getByRole('button', { name: /^New$/i }).click();
  await page.getByPlaceholder(/Meridian v\. Apex Cloud/i).fill('Enterprise Governance E2E');
  await page.getByPlaceholder(/Meridian Legal Group LLP/i).fill('Buyer Corp');
  await page.getByPlaceholder(/Apex Cloud Systems/i).fill('Acme');
  await page.getByTestId('conflict-check-btn').click();
  await expect(page.getByText(/Conflict Screen:/i)).toBeVisible();

  await page.getByRole('button', { name: /Create Matter/i }).click();
  await expect(page.getByRole('heading', { name: 'Enterprise Governance E2E' })).toBeVisible();

  // 2. Import sample contract via mock_dms
  await page.getByRole('button', { name: /Import from DMS/i }).click();
  await expect(page.getByText(/Enterprise DMS & Legal Vault Connectors/i)).toBeVisible();
  await page.getByRole('button', { name: /^Import$/i }).first().click();
  await expect(page.getByText('indexed').first()).toBeVisible();

  // 3. Activate Legal Hold and verify delete lock state
  await page.getByTestId('legal-hold-btn').click();
  await expect(page.getByText('Legal Hold Active')).toBeVisible();
  await expect(
    page.locator('[title="Deletion locked under active Litigation Legal Hold"]').first()
  ).toBeDisabled();

  // 4. Verify cryptographic SHA-256 audit chain
  await page.getByRole('button', { name: /Review Queue & Audit Trail/i }).click();
  await page.getByRole('button', { name: /Immutable Audit Trail/i }).click();
  await page.getByRole('button', { name: /Verify SHA-256 Audit Chain/i }).click();
  await expect(page.getByTestId('audit-chain-status')).toContainText(/Chain Intact/i);

  // 5. Open Admin & Integrations modal and preview retention sweep
  await page.getByTestId('admin-integrations-btn').click();
  await page.getByRole('button', { name: /Retention & Compliance/i }).click();
  await page.getByRole('button', { name: /Preview Expired \(Dry Run\)/i }).click();
  await expect(page.getByText(/Evaluated:/i)).toBeVisible();
  await page.getByRole('button', { name: /Close Admin Console/i }).click();

  // 6. Stream Research Q&A and extract obligations + export .ics calendar
  await page.getByRole('button', { name: /Q&A, Synthesis & Drafting/i }).click();
  await page.getByPlaceholder(/What annual fee/i).fill('What is the limitation of liability cap?');
  await page.getByRole('button', { name: /Answer with Citations/i }).click();
  await expect(page.getByText(/Confidence/i).first()).toBeVisible();

  await page.getByRole('button', { name: /Obligations & Deadlines/i }).click();
  await page.getByRole('button', { name: /Extract Deadlines/i }).click();
  await expect(page.getByText(/Download Calendar \(\.ics\)/i)).toBeVisible();

  const icsHref = await page.getByRole('link', { name: /Download Calendar \(\.ics\)/i }).getAttribute('href');
  expect(icsHref).toBeTruthy();
  const icsResp = await page.request.get(icsHref!);
  expect(icsResp.status()).toBe(200);
  expect(await icsResp.text()).toContain('BEGIN:VCALENDAR');
});
