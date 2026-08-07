import { expect, type Locator, type Page } from '@playwright/test';

export const statusLabels = [
  'Novo',
  'Aplicado',
  'Entrevista',
  'Recusado',
  'Fechada',
] as const;

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

export function firstJobButton(page: Page): Locator {
  return page.locator('main button').filter({
    has: page.locator('h1, h2, h3, h4, h5, h6'),
  }).first();
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
  const buttons = page.locator('main button');
  const texts = await buttons.allTextContents();

  return texts
    .map(text => {
      const match = text.trim().match(/^(\d{1,3})\b/);
      return match ? Number(match[1]) : NaN;
    })
    .filter(Number.isFinite);
}

export async function selectJobStatus(
  page: Page,
  label: (typeof statusLabels)[number]
) {
  const select = firstStatusSelect(page);
  await expect(select).toBeVisible();
  await select.selectOption({ label });
  await expect(select).toHaveValue(await select.inputValue());
}
