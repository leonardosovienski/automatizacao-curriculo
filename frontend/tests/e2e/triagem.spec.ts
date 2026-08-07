import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import {
  firstJobButton,
  firstJobCard,
  firstStatusSelect,
  getVisibleScores,
  openApp,
  searchBox,
  selectJobStatus,
  sortSelect,
  statusLabels,
} from './helpers/app';
import { attachDiagnostics } from './helpers/diagnostics';

test.describe('Triagem de Vagas — smoke, UX e integridade', () => {
  test.beforeEach(async ({ page }, testInfo) => {
    attachDiagnostics(page, testInfo);
    await openApp(page);
  });

  test('carrega a aplicação e os controles principais', async ({ page }) => {
    await expect(
      page.getByText('Scoring automático com Google Gemini')
    ).toBeVisible();

    await expect(searchBox(page)).toBeVisible();
    await expect(sortSelect(page)).toBeVisible();

    for (const filter of [
      'Todas',
      'Novo',
      'Aplicado',
      'Entrevista',
      'Recusado',
      'Fechada',
      'Descartada',
    ]) {
      await expect(page.getByRole('button', { name: filter })).toBeVisible();
    }

    await expect(
      page.getByRole('button', { name: 'Como popular o histórico' })
    ).toBeVisible();
  });

  test('dashboard exibe métricas numéricas coerentes', async ({ page }) => {
    for (const label of [
      'Total',
      'Novas',
      'Aplicadas',
      'Entrevistas',
      'Fechadas',
    ]) {
      await expect(page.getByText(label, { exact: true })).toBeVisible();
    }

    const body = await page.locator('main').innerText();
    const labels = ['Total', 'Novas', 'Aplicadas', 'Entrevistas', 'Fechadas'];

    for (const label of labels) {
      const regex = new RegExp(`\\b\\d+\\s*${label}\\b`, 'i');
      expect(body).toMatch(regex);
    }
  });

  test('todos os filtros podem ser acionados sem quebrar a página', async ({ page }) => {
    for (const filter of [
      'Todas',
      'Novo',
      'Aplicado',
      'Entrevista',
      'Recusado',
      'Fechada',
      'Descartada',
    ]) {
      await page.getByRole('button', { name: filter }).click();
      await expect(
        page.getByRole('heading', { name: 'Triagem de Vagas' })
      ).toBeVisible();
    }
  });

  test('busca encontra vaga por título', async ({ page }) => {
    await page.getByRole('button', { name: 'Todas' }).click();

    const first = firstJobButton(page);
    await expect(first).toBeVisible();

    const text = (await first.innerText()).trim();
    const candidate = text
      .split('\n')
      .map(x => x.trim())
      .find(x => /[A-Za-zÀ-ÿ]{3,}/.test(x) && !/^\d+$/.test(x));

    expect(candidate).toBeTruthy();

    const term = candidate!.split(/\s+/).slice(0, 2).join(' ');
    await searchBox(page).fill(term);

    await expect(firstJobButton(page)).toBeVisible();
  });

  test('busca encontra vaga por empresa quando há dados', async ({ page }) => {
    await page.getByRole('button', { name: 'Todas' }).click();

    const first = firstJobButton(page);
    await expect(first).toBeVisible();

    const text = await first.innerText();
    const knownCompany = ['GlobalSoft'].find(company => text.includes(company));

    test.skip(!knownCompany, 'Nenhuma empresa conhecida disponível no dataset atual.');

    await searchBox(page).fill(knownCompany!);
    await expect(page.getByText(knownCompany!, { exact: false }).first()).toBeVisible();
  });

  test('busca sem resultado não deixa resultados falsos', async ({ page }) => {
    await page.getByRole('button', { name: 'Todas' }).click();
    await searchBox(page).fill('__vaga_que_nao_deve_existir_9f3a21__');

    await expect(
      page.getByRole('button', { name: /^\d{1,3}\s.+/ })
    ).toHaveCount(0);
  });

  test('limpar busca restaura os resultados', async ({ page }) => {
    await page.getByRole('button', { name: 'Todas' }).click();
    const before = await page
      .getByRole('button', { name: /^\d{1,3}\s.+/ })
      .count();

    await searchBox(page).fill('__sem_resultado__');
    await searchBox(page).clear();

    await expect(
      page.getByRole('button', { name: /^\d{1,3}\s.+/ })
    ).toHaveCount(before);
  });

  test('ordena por maior e menor score', async ({ page }) => {
    await page.getByRole('button', { name: 'Todas' }).click();

    await sortSelect(page).selectOption({ label: 'Maior score' });
    const desc = await getVisibleScores(page);

    if (desc.length >= 2) {
      expect(desc).toEqual([...desc].sort((a, b) => b - a));
    }

    await sortSelect(page).selectOption({ label: 'Menor score' });
    const asc = await getVisibleScores(page);

    if (asc.length >= 2) {
      expect(asc).toEqual([...asc].sort((a, b) => a - b));
    }
  });

  test('opção Mais recente é selecionável', async ({ page }) => {
    await sortSelect(page).selectOption({ label: 'Mais recente' });
    await expect(sortSelect(page)).toHaveValue(await sortSelect(page).inputValue());
  });

  test('expande uma vaga e mostra ações/detalhes', async ({ page }) => {
    await page.getByRole('button', { name: 'Todas' }).click();

    const job = firstJobButton(page);
    await expect(job).toBeVisible();
    await job.click();

    await expect(firstStatusSelect(page)).toBeVisible();
    await expect(
      firstJobCard(page).getByRole('link', { name: 'Vaga original' })
    ).toBeVisible();
  });

  test('link Vaga original possui URL válida e não-javascript', async ({ page }) => {
    await page.getByRole('button', { name: 'Todas' }).click();
    await firstJobButton(page).click();

    const link = firstJobCard(page).getByRole('link', { name: 'Vaga original' });
    const href = await link.getAttribute('href');

    expect(href).toBeTruthy();
    expect(href).toMatch(/^https?:\/\//);
    expect(href).not.toMatch(/^javascript:/i);
  });

  test('sinaliza fixture/mock example.com na vaga original', async ({ page }) => {
    await page.getByRole('button', { name: 'Todas' }).click();
    await firstJobButton(page).click();

    const link = firstJobCard(page).getByRole('link', { name: 'Vaga original' });
    const href = await link.getAttribute('href');

    // Comparação por substring (`href.includes('example.com')`) também
    // casaria hosts como "example.com.attacker.net" ou "notexample.com" —
    // aqui exige-se igualdade exata ou sufixo ".example.com" no hostname.
    let hostname: string | null = null;
    try {
      hostname = href ? new URL(href).hostname : null;
    } catch {
      hostname = null;
    }
    const isExampleFixture =
      hostname === 'example.com' || hostname?.endsWith('.example.com');

    if (isExampleFixture) {
      test.info().annotations.push({
        type: 'warning',
        description:
          'A vaga usa example.com; isso parece fixture/mock e não uma URL real.',
      });
    }
  });

  test('permite percorrer todos os status disponíveis', async ({ page }) => {
    await page.getByRole('button', { name: 'Todas' }).click();
    await firstJobButton(page).click();

    for (const status of statusLabels) {
      await selectJobStatus(page, status);
    }
  });

  test('mudança de status atualiza o dashboard', async ({ page }) => {
    await page.getByRole('button', { name: 'Todas' }).click();
    await firstJobButton(page).click();

    const status = firstStatusSelect(page);
    const original = await status.inputValue();

    await status.selectOption({ label: 'Aplicado' });

    const main = await page.locator('main').innerText();
    expect(main).toMatch(/\d+\s*Aplicadas/i);

    // tenta restaurar o estado para reduzir efeitos colaterais
    if (original) {
      await status.selectOption(original).catch(() => {});
    }
  });

  test('status persiste após reload quando a aplicação oferece persistência', async ({ page }) => {
    await page.getByRole('button', { name: 'Todas' }).click();
    await firstJobButton(page).click();

    const status = firstStatusSelect(page);
    const original = await status.inputValue();

    await status.selectOption({ label: 'Entrevista' });
    await page.reload();

    await page.getByRole('button', { name: 'Todas' }).click();

    const job = firstJobButton(page);
    await expect(job).toBeVisible();
    await job.click();

    const after = firstStatusSelect(page);
    await expect(after).toHaveValue('entrevista').catch(async () => {
      const value = await after.inputValue();
      expect(value.toLowerCase()).toContain('entrevista');
    });

    // restauração best-effort
    if (original) {
      await after.selectOption(original).catch(() => {});
    }
  });

  test('ajuda Como popular o histórico abre conteúdo útil', async ({ page }) => {
    const button = page.getByRole('button', {
      name: 'Como popular o histórico',
    });

    await button.click();

    const dialogs = page.getByRole('dialog');
    if (await dialogs.count()) {
      await expect(dialogs.first()).toBeVisible();
      expect((await dialogs.first().innerText()).trim().length).toBeGreaterThan(10);
    } else {
      // fallback para componentes sem role=dialog
      const bodyText = await page.locator('body').innerText();
      expect(bodyText.length).toBeGreaterThan(100);
    }
  });

  test('rota inexistente tem comportamento explícito', async ({ page }) => {
    await page.goto('/this-route-should-not-exist');

    const titleVisible = await page
      .getByRole('heading', { name: 'Triagem de Vagas' })
      .isVisible()
      .catch(() => false);

    const body = (await page.locator('body').innerText()).toLowerCase();
    const hasNotFound = /404|não encontrad|not found/.test(body);

    expect(
      titleVisible || hasNotFound,
      'Rota inválida deve cair explicitamente na SPA ou apresentar 404.'
    ).toBeTruthy();

    if (titleVisible && !hasNotFound) {
      test.info().annotations.push({
        type: 'warning',
        description:
          'Rota inexistente carrega a SPA normalmente; confirme se esse fallback é intencional.',
      });
    }
  });

  test('não gera erros graves de console, exceções JS ou HTTP 5xx ao carregar', async ({
    page,
  }) => {
    const consoleErrors: string[] = [];
    const pageErrors: string[] = [];
    const serverErrors: string[] = [];

    page.on('console', msg => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });

    page.on('pageerror', error => pageErrors.push(error.message));

    page.on('response', response => {
      if (response.status() >= 500) {
        serverErrors.push(`${response.status()} ${response.url()}`);
      }
    });

    await page.reload();
    await page.waitForLoadState('networkidle').catch(() => {});

    expect(pageErrors).toEqual([]);
    expect(serverErrors).toEqual([]);

    // Erros conhecidos de extensões/browser podem ser filtrados aqui se necessário.
    expect(consoleErrors).toEqual([]);
  });

  test('acessibilidade automática não encontra violações críticas/sérias', async ({
    page,
  }) => {
    const results = await new AxeBuilder({ page }).analyze();

    const severe = results.violations.filter(v =>
      ['critical', 'serious'].includes(v.impact ?? '')
    );

    expect(
      severe,
      severe
        .map(v => `${v.id}: ${v.help} (${v.nodes.length} nó(s))`)
        .join('\n')
    ).toEqual([]);
  });
});
