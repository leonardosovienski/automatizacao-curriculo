import { test, expect } from '@playwright/test';
import { openApp, searchBox, sortSelect } from './helpers/app';

test.describe('Responsividade', () => {
  test('não cria overflow horizontal relevante', async ({ page }) => {
    await openApp(page);

    const overflow = await page.evaluate(() => {
      return document.documentElement.scrollWidth - document.documentElement.clientWidth;
    });

    expect(overflow).toBeLessThanOrEqual(2);
  });

  test('controles essenciais continuam utilizáveis no viewport atual', async ({ page }) => {
    await openApp(page);

    await expect(searchBox(page)).toBeVisible();
    await expect(sortSelect(page)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Todas' })).toBeVisible();
  });
});
