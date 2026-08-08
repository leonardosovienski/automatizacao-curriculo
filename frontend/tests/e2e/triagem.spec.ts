import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import {
  firstJobButton,
  firstJobCard,
  firstStatusSelect,
  getVisibleScores,
  jobButtons,
  openApp,
  searchBox,
  SEED,
  selectJobStatus,
  sortSelect,
  statValue,
  statusLabels,
  STATUS_POR_ROTULO,
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

    // "Total" é o único contador estável entre testes (os demais mudam conforme
    // os testes de status mutam o seed), então é nele que dá para afirmar valor.
    expect(await statValue(page, 'Total')).toBe(SEED.total);

    for (const label of ['Novas', 'Aplicadas', 'Entrevistas', 'Fechadas']) {
      expect(await statValue(page, label)).toBeGreaterThanOrEqual(0);
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

  // O seed (fixtures/historico.seed.json) é fixo e determinístico: dava para
  // afirmar o resultado exato. O `test.skip` que existia aqui transformava uma
  // regressão do fixture em teste verde em vez de vermelho.
  test('busca por empresa isola a vaga daquela empresa', async ({ page }) => {
    await page.getByRole('button', { name: 'Todas' }).click();
    await expect(jobButtons(page)).toHaveCount(SEED.total);

    await searchBox(page).fill('DataForge');

    await expect(jobButtons(page)).toHaveCount(1);
    await expect(page.getByRole('heading', { name: SEED.recente.titulo })).toBeVisible();
    await expect(
      page.getByRole('heading', { name: SEED.maiorScore.titulo })
    ).toHaveCount(0);
  });

  test('busca sem resultado não deixa resultados falsos', async ({ page }) => {
    await page.getByRole('button', { name: 'Todas' }).click();
    await expect(jobButtons(page)).toHaveCount(SEED.total);

    await searchBox(page).fill('__vaga_que_nao_deve_existir_9f3a21__');

    await expect(jobButtons(page)).toHaveCount(0);
  });

  test('limpar busca restaura os resultados', async ({ page }) => {
    await page.getByRole('button', { name: 'Todas' }).click();
    // Contagem literal do seed em vez de "o que estiver na tela": o locator
    // antigo (nome começando em dígito) ignorava a vaga descartada, que mostra
    // "—" no lugar do score, então before/after podiam ser 0 e o teste passava.
    await expect(jobButtons(page)).toHaveCount(SEED.total);

    await searchBox(page).fill('__sem_resultado__');
    await expect(jobButtons(page)).toHaveCount(0);

    await searchBox(page).clear();
    await expect(jobButtons(page)).toHaveCount(SEED.total);
  });

  test('ordena por maior e menor score', async ({ page }) => {
    await page.getByRole('button', { name: 'Todas' }).click();

    // Sem esta garantia as asserções abaixo passavam por vacuidade: com menos
    // de dois scores visíveis, "está ordenado" é verdade para qualquer coisa.
    await expect(jobButtons(page)).toHaveCount(SEED.total);

    await sortSelect(page).selectOption({ label: 'Maior score' });
    expect(await getVisibleScores(page)).toEqual([...SEED.scoresVisiveis]);

    await sortSelect(page).selectOption({ label: 'Menor score' });
    expect(await getVisibleScores(page)).toEqual([...SEED.scoresVisiveis].reverse());
  });

  test('Mais recente ordena por data, não por score', async ({ page }) => {
    await page.getByRole('button', { name: 'Todas' }).click();

    await sortSelect(page).selectOption({ label: 'Maior score' });
    await expect(firstJobButton(page)).toContainText(SEED.maiorScore.titulo);

    await sortSelect(page).selectOption({ label: 'Mais recente' });
    // A vaga mais recente tem score MENOR: se a ordenação por data não
    // estivesse implementada, o primeiro cartão continuaria sendo o de score 95.
    await expect(firstJobButton(page)).toContainText(SEED.recente.titulo);
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

  // Antes este teste só empurrava uma annotation e não tinha asserção nenhuma:
  // passava com qualquer href, inclusive nenhum. Agora verifica de ponta a ponta
  // que o link do histórico chega íntegro ao DOM (API -> types.ts -> VagaCard).
  test('link da vaga chega ao DOM exatamente como está no histórico', async ({ page }) => {
    await page.getByRole('button', { name: 'Todas' }).click();
    await sortSelect(page).selectOption({ label: 'Maior score' });

    const link = firstJobCard(page).getByRole('link', { name: 'Vaga original' });
    const href = await link.getAttribute('href');

    expect(href).toBe('https://example.com/vaga/abc');

    // Comparação por substring (`href.includes('example.com')`) também
    // casaria hosts como "example.com.attacker.net" ou "notexample.com" —
    // aqui exige-se igualdade exata ou sufixo ".example.com" no hostname.
    const hostname = new URL(href!).hostname;
    expect(hostname === 'example.com' || hostname.endsWith('.example.com')).toBe(true);

    // abrir em nova aba sem rel=noreferrer vaza a origem via window.opener
    await expect(link).toHaveAttribute('rel', /noreferrer/);
  });

  test('permite percorrer todos os status disponíveis', async ({ page }) => {
    await page.getByRole('button', { name: 'Todas' }).click();
    await firstJobButton(page).click();

    for (const status of statusLabels) {
      await selectJobStatus(page, status);
    }
  });

  test('mudança de status incrementa o contador do dashboard', async ({ page }) => {
    await page.getByRole('button', { name: 'Todas' }).click();
    await sortSelect(page).selectOption({ label: 'Maior score' });

    const status = firstStatusSelect(page);

    // Parte de um estado conhecido: se a vaga já estivesse "aplicado", marcar
    // "Aplicado" de novo não mudaria contador nenhum e o teste passaria à toa.
    await status.selectOption({ label: 'Novo' });
    await expect(status).toHaveValue(STATUS_POR_ROTULO.Novo);
    const antes = await statValue(page, 'Aplicadas');

    await status.selectOption({ label: 'Aplicado' });

    // A regex antiga (/\d+\s*Aplicadas/) casava com "0 Aplicadas" e portanto
    // passava mesmo se o contador nunca subisse.
    await expect.poll(() => statValue(page, 'Aplicadas')).toBe(antes + 1);
    await expect(status).toHaveValue(STATUS_POR_ROTULO.Aplicado);

    await status.selectOption({ label: 'Novo' });
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
