import { expect, test } from '@playwright/test';
import type { PerfilUsuario, VagaResumo } from '../../src/types';
import { openApp, SEED } from './helpers/app';

const apiUrl = process.env.E2E_API_URL ?? 'http://127.0.0.1:8000';
const baseUrl = process.env.E2E_BASE_URL ?? 'http://127.0.0.1:5173';
const headers = { Origin: baseUrl };

function barreira() {
  let liberar!: () => void;
  const pronta = new Promise<void>((resolve) => { liberar = resolve; });
  return { pronta, liberar };
}

test('resposta inicial atrasada não apaga a busca recém-iniciada', async ({ page }) => {
  const respostaInicial = barreira();
  const snapshotObtido = barreira();
  // Authentication, onboarding and the initial snapshot use the real API.
  // Only the job execution is simulated so no external AI service runs.
  await page.route('**/api/buscas/atual', async (route) => {
    const response = await route.fetch();
    expect(response.ok()).toBeTruthy();
    expect(await response.json()).toBeNull();
    snapshotObtido.liberar();
    await respostaInicial.pronta;
    await route.fulfill({ response });
  });
  const job = {
    id: 'regressao-busca', tipo: 'busca', vaga_alvo_id: null, resultado: null,
    pedido: null, limite: 10, estado: 'pendente', progresso: 0,
    mensagem: 'Aguardando processamento.', erro: null, encontradas: 0,
    criada_em: new Date().toISOString(), concluida_em: null,
  };
  await page.route('**/api/buscas', (route) => route.request().method() === 'POST'
    ? route.fulfill({ status: 202, json: job }) : route.continue());
  await page.route('**/api/buscas/regressao-busca', (route) => route.fulfill({
    json: { ...job, estado: 'processando', progresso: 25, mensagem: 'Busca continua após o retorno inicial.' },
  }));
  try {
    await openApp(page);
    await snapshotObtido.pronta;
    const buscar = page.getByRole('button', { name: 'Buscar vagas', exact: true });
    await buscar.click();
    await expect(page.getByRole('status')).toContainText('Aguardando processamento.');
    await expect(buscar).toBeDisabled();
    respostaInicial.liberar();
    // Waiting for a subsequent poll proves the late response did not cancel
    // monitoring; asserting the old message immediately would miss the race.
    await expect(page.getByRole('status')).toContainText('Busca continua após o retorno inicial.');
    await expect(page.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '25');
    await expect(buscar).toBeDisabled();
  } finally { respostaInicial.liberar(); }
});

test('PATCH atrasado mantém a vaga ao trocar de Novo para Todas', async ({ page }) => {
  await openApp(page);
  const listagem = await page.request.get(`${apiUrl}/api/vagas`);
  expect(listagem.ok()).toBeTruthy();
  const vaga = (await listagem.json() as VagaResumo[]).find((item) => item.titulo === SEED.maiorScore.titulo)!;
  expect(vaga).toBeTruthy();
  const statusUrl = `${apiUrl}/api/vagas/${vaga.id}/status`;
  const respostaPatch = barreira();
  const persistido = barreira();
  try {
    expect((await page.request.patch(statusUrl, { headers, data: { status: 'novo' } })).ok()).toBeTruthy();
    // Commit the real PATCH, hold only its HTTP response, and let the new
    // filter read the committed row from the real database.
    await page.route(`**/api/vagas/${vaga.id}/status`, async (route) => {
      const response = await route.fetch();
      expect(response.ok()).toBeTruthy();
      persistido.liberar();
      await respostaPatch.pronta;
      await route.fulfill({ response });
    });
    await page.reload();
    const select = page.getByRole('combobox', { name: `Status da vaga ${vaga.titulo}`, exact: true });
    await expect(select).toHaveValue('novo');
    await page.waitForLoadState('networkidle');
    await select.selectOption('aplicado');
    await persistido.pronta;
    const statsDoFiltro = page.waitForResponse((response) => response.url().endsWith('/api/stats'));
    await page.getByRole('button', { name: 'Todas', exact: true }).click();
    await expect(select).toHaveValue('aplicado');
    // Drain the filter's initial reads before releasing the delayed PATCH.
    await (await statsDoFiltro).finished();
    respostaPatch.liberar();
    await expect(select).toBeEnabled();
    await expect(page.getByRole('button', { name: 'Todas', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByRole('heading', { name: vaga.titulo, exact: true })).toBeVisible();
    await expect(select).toHaveValue('aplicado');
    await page.reload();
    await page.getByRole('button', { name: 'Todas', exact: true }).click();
    await expect(select).toHaveValue('aplicado');
  } finally {
    respostaPatch.liberar();
    expect((await page.request.patch(statusUrl, { headers, data: { status: vaga.status } })).ok()).toBeTruthy();
  }
});

test('retira consentimento salvo com CV curto e edição inválida sem salvar o rascunho', async ({ page }) => {
  await openApp(page);
  const perfilResponse = await page.request.get(`${apiUrl}/api/perfil`);
  const cvResponse = await page.request.get(`${apiUrl}/api/cv`);
  expect(perfilResponse.ok()).toBeTruthy();
  expect(cvResponse.ok()).toBeTruthy();
  const perfil = await perfilResponse.json() as PerfilUsuario;
  const cvOriginal = await cvResponse.json();
  const cvCurto = 'Experiência com Python.';
  const gravacoesCV: string[] = [];
  try {
    expect((await page.request.put(`${apiUrl}/api/perfil`, { headers, data: { ...perfil, consentimento_ia: true } })).ok()).toBeTruthy();
    expect((await page.request.put(`${apiUrl}/api/cv`, { headers, data: { conteudo: cvCurto } })).ok()).toBeTruthy();
    // No intercepted responses: profile/CV persistence, consent, auth and
    // backend validation all participate in this regression.
    page.on('request', (request) => {
      if (request.method() === 'PUT' && request.url().endsWith('/api/cv')) gravacoesCV.push(request.url());
    });
    await page.getByRole('button', { name: 'Configurações do perfil', exact: true }).click();
    await expect(page.getByLabel('Currículo-base')).toHaveValue(cvCurto);
    await page.getByLabel('Nome', { exact: true }).clear();
    await page.getByLabel('Currículo-base').clear();
    const consentimento = page.getByRole('checkbox', { name: /Autorizo o envio/ });
    await consentimento.uncheck();
    await expect(page.getByRole('status')).toContainText('Permissão retirada.');
    const salvo = await page.request.get(`${apiUrl}/api/perfil`);
    expect(salvo.ok()).toBeTruthy();
    expect(await salvo.json()).toEqual({ ...perfil, consentimento_ia: false });
    expect(await (await page.request.get(`${apiUrl}/api/cv`)).json()).toEqual({ conteudo: cvCurto });
    expect(gravacoesCV).toEqual([]);
    await expect(page.getByLabel('Nome', { exact: true })).toHaveValue('');
    await page.getByRole('button', { name: 'Cancelar', exact: true }).click();
    await page.reload();
    await page.getByRole('button', { name: 'Configurações do perfil', exact: true }).click();
    await expect(consentimento).not.toBeChecked();
    await expect(page.getByLabel('Nome', { exact: true })).toHaveValue(perfil.nome);
    await expect(page.getByLabel('Currículo-base')).toHaveValue(cvCurto);
  } finally {
    expect((await page.request.put(`${apiUrl}/api/cv`, { headers, data: cvOriginal })).ok()).toBeTruthy();
    expect((await page.request.put(`${apiUrl}/api/perfil`, { headers, data: perfil })).ok()).toBeTruthy();
  }
});
