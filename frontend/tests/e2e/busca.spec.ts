import { expect, test } from '@playwright/test';
import { openApp } from './helpers/app';

test('inicia e acompanha uma busca pelo navegador', async ({ page }) => {
  let consultas = 0;
  const base = {
    id: 'busca-e2e', pedido: 'Python remoto', limite: 10,
    erro: null, encontradas: 0, criada_em: new Date().toISOString(), concluida_em: null,
  };
  await page.route('**/api/buscas/atual', route => route.fulfill({ json: null }));
  await page.route('**/api/buscas', async route => {
    if (route.request().method() !== 'POST') return route.continue();
    expect((await route.request().postDataJSON()).pedido).toBe('Python remoto');
    await route.fulfill({ status: 202, json: { ...base, estado: 'pendente', progresso: 0, mensagem: 'Aguardando processamento.' } });
  });
  await page.route('**/api/buscas/busca-e2e', route => {
    consultas += 1;
    return route.fulfill({ json: { ...base, estado: 'concluida', progresso: 100, mensagem: 'Busca concluída: 2 vaga(s) processada(s).', encontradas: 2, concluida_em: new Date().toISOString() } });
  });

  await openApp(page);
  await page.getByLabel('Preferências desta busca').fill('Python remoto');
  await page.getByRole('button', { name: 'Buscar vagas', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('Aguardando processamento.');
  await expect(page.getByRole('status')).toContainText('Busca concluída', { timeout: 5_000 });
  expect(consultas).toBeGreaterThan(0);
});
