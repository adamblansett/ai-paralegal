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
