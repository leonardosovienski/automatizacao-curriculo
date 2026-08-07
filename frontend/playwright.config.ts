import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  // O fixture (tests/e2e/fixtures/historico.seed.json) tem uma única vaga
  // "viva" (a outra é descartada) — a maioria dos testes muta o status
  // dela via PATCH real na API. Rodar em paralelo faz dois testes de
  // status colidirem no mesmo registro (last-write-wins), derrubando
  // asserções de forma não-determinística. Até o fixture ganhar um
  // registro isolado por teste, workers=1 é o jeito correto de garantir
  // que a suíte não seja flaky — velocidade cede para correção aqui.
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: [
    ['list'],
    ['html', { outputFolder: 'playwright-report', open: 'never' }],
  ],

  expect: {
    timeout: 7_500,
  },

  timeout: 30_000,

  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://127.0.0.1:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    actionTimeout: 10_000,
    navigationTimeout: 15_000,
  },

  // Dois servidores: a API sobe isolada de qualquer historico.json real do
  // usuário, populada a partir de um fixture estático (ver api/seed_e2e.py e
  // tests/e2e/fixtures/historico.seed.json) — sem chamadas ao Gemini durante
  // o E2E. E2E_SKIP_WEBSERVER=1 pula os dois (útil se já estiverem no ar).
  webServer: process.env.E2E_SKIP_WEBSERVER
    ? undefined
    : [
        {
          command: `${process.env.E2E_PYTHON ?? 'python'} api/seed_e2e.py`,
          cwd: '..',
          env: {
            TRIAGEM_HISTORICO: '.e2e-historico.json',
            E2E_API_PORT: '8000',
          },
          url: 'http://127.0.0.1:8000/health',
          reuseExistingServer: !process.env.CI,
          timeout: 60_000,
        },
        {
          command: 'npm run dev -- --host 127.0.0.1',
          env: { VITE_API_URL: process.env.VITE_API_URL ?? 'http://127.0.0.1:8000' },
          url: 'http://127.0.0.1:5173',
          reuseExistingServer: !process.env.CI,
          timeout: 120_000,
        },
      ],

  projects: [
    {
      name: 'desktop-chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'mobile-chromium',
      use: { ...devices['Pixel 7'] },
    },
  ],

  outputDir: 'test-results',
});
