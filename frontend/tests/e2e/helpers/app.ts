import { expect, type Locator, type Page } from '@playwright/test';

// Rótulo visível -> valor que o <select> realmente carrega (types.ts:STATUS_LABEL).
// Ter o par explícito é o que permite afirmar que a troca de status PEGOU, em vez
// de reler o próprio valor e comparar consigo mesmo.
export const STATUS_POR_ROTULO = {
  Novo: 'novo',
  Aplicado: 'aplicado',
  Entrevista: 'entrevista',
  Recusado: 'recusado',
  Fechada: 'fechada',
} as const;

export const statusLabels = Object.keys(
  STATUS_POR_ROTULO
) as (keyof typeof STATUS_POR_ROTULO)[];

export async function openApp(page: Page) {
  await page.goto('/');
  await expect(
    page.getByRole('heading', { name: 'Triagem de Vagas' })
  ).toBeVisible();
}

export function searchBox(page: Page) {
  return page.getByRole('searchbox', {
    name: 'Buscar por empresa ou título',
  });
}

export function sortSelect(page: Page) {
  return page.getByRole('combobox', { name: 'Ordenar vagas' });
}

/** Espelho do fixture (fixtures/historico.seed.json), que é fixo por design.
 *
 * Os três registros existem para que "Maior score" e "Mais recente" produzam
 * ordens DIFERENTES — com dados de mesma data qualquer ordenação passaria. */
export const SEED = {
  total: 3,
  /** maior score (95) e data intermediária */
  maiorScore: { titulo: 'Platform Engineer Intern', empresa: 'GlobalSoft', score: 95 },
  /** score menor (72), porém a mais recente */
  recente: { titulo: 'Backend Engineer Pleno', empresa: 'DataForge', score: 72 },
  /** descartada: sem score, a mais antiga */
  descartada: { titulo: 'Desenvolvedor Java Sênior', empresa: 'ConsultCo' },
  /** scores numéricos visíveis (a descartada mostra "—", não entra) */
  scoresVisiveis: [95, 72],
} as const;

/** Todos os botões-cartão de vaga (inclui a descartada, que não tem score). */
export function jobButtons(page: Page): Locator {
  return page.locator('main button').filter({
    has: page.locator('h1, h2, h3, h4, h5, h6'),
  });
}

export function firstJobButton(page: Page): Locator {
  return jobButtons(page).first();
}

// O card da vaga é o ancestral do botão de expandir (2 níveis acima: a
// linha do header, depois o container do card). "Vaga original" fica
// sempre visível no header do card — não só quando expandido — então um
// locator page-wide dá match em todo card visível ao mesmo tempo (ex.:
// com o filtro "Todas"). Escopar no card resolve isso sem depender de
// classes CSS.
export function firstJobCard(page: Page): Locator {
  return firstJobButton(page).locator('xpath=../..');
}

export function firstStatusSelect(page: Page): Locator {
  return page
    .getByRole('combobox', { name: /^Status da vaga / })
    .first();
}

export async function getVisibleJobTitles(page: Page): Promise<string[]> {
  return page
    .locator('main h1, main h2, main h3, main h4, main h5, main h6')
    .allTextContents();
}

export async function getVisibleScores(page: Page): Promise<number[]> {
  const texts = await jobButtons(page).allTextContents();

  return texts
    .map(text => {
      // `\b` NÃO servia aqui: o texto do cartão é "95Platform Engineer..." e
      // não há fronteira de palavra entre "5" e "P" (ambos são \w), então a
      // regex antiga nunca casava e esta função devolvia sempre [] — o que
      // fazia o teste de ordenação passar por vacuidade.
      const match = text.trim().match(/^(\d{1,3})(?=\D|$)/);
      return match ? Number(match[1]) : NaN;
    })
    .filter(Number.isFinite);
}

export async function selectJobStatus(
  page: Page,
  label: keyof typeof STATUS_POR_ROTULO
) {
  const select = firstStatusSelect(page);
  await expect(select).toBeVisible();
  await select.selectOption({ label });
  // Valor esperado, não `await select.inputValue()`: comparar o campo consigo
  // mesmo passava mesmo se o PATCH falhasse e a UI revertesse a seleção.
  await expect(select).toHaveValue(STATUS_POR_ROTULO[label]);
}

/** Lê o número exibido num cartão do dashboard (StatsBar). */
export async function statValue(page: Page, label: string): Promise<number> {
  const numero = page
    .getByText(label, { exact: true })
    .locator('xpath=preceding-sibling::div[1]');
  return Number((await numero.innerText()).trim());
}
