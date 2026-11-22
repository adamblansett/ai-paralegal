import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig, devices } from '@playwright/test';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const backendDir = path.resolve(__dirname, '../backend');

const winPython = path.join(backendDir, '.venv', 'Scripts', 'python.exe');
const nixVenvPython = path.join(backendDir, '.venv', 'bin', 'python');
const pythonExe =
  process.platform === 'win32'
    ? winPython
    : fs.existsSync(nixVenvPython)
    ? nixVenvPython
    : 'python3';

const e2eDbPath = path.join(os.tmpdir(), 'ai-paralegal-playwright-e2e.db');
const e2eStorageDir = path.join(os.tmpdir(), 'ai-paralegal-playwright-storage');
try {
  fs.rmSync(e2eDbPath, { force: true });
  fs.rmSync(e2eStorageDir, { recursive: true, force: true });
} catch {
  // ignore cleanup if locked
}

const dbUrl = `sqlite+aiosqlite:///${e2eDbPath.replace(/\\/g, '/')}`;

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 45_000,
  expect: {
    timeout: 10_000,
  },
  use: {
    baseURL: 'http://127.0.0.1:5174',
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: [
    {
      command: `"${pythonExe}" -m alembic upgrade head && "${pythonExe}" -m uvicorn app.main:app --host 127.0.0.1 --port 8011`,
      cwd: backendDir,
      url: 'http://127.0.0.1:8011/api/v1/health',
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        ...process.env,
        APP_ENV: 'test',
        DATABASE_URL: dbUrl,
        STORAGE_BACKEND: 'local',
        LOCAL_STORAGE_DIR: e2eStorageDir,
        TASK_EXECUTION_MODE: 'inline',
        LLM_DEFAULT_PROVIDER: 'mock',
        EMBEDDING_PROVIDER: 'deterministic',
        AUTH_AUTO_SEED_DEMO_USERS: 'true',
        RATE_LIMIT_ENABLED: 'false',
      },
    },
    {
      command: 'npx vite --host 127.0.0.1 --port 5174 --strictPort',
      cwd: __dirname,
      url: 'http://127.0.0.1:5174',
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        ...process.env,
        VITE_API_TARGET: 'http://127.0.0.1:8011',
      },
    },
  ],
});
