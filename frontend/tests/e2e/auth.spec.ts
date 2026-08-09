import { expect, test } from '@playwright/test';

test('novo usuário cria conta e recebe onboarding isolado', async ({ page }, testInfo) => {
  const nome = `${testInfo.project.name}-${Date.now()}`;
  await page.goto('/');
  await expect(page.getByText('Entre na sua conta')).toBeVisible();
  await page.getByRole('button', { name: 'Criar uma conta' }).click();
  await page.getByLabel('E-mail').fill(`${nome}@example.com`);
  await page.getByLabel('Senha').fill('senha-nova-123');
  await page.getByRole('button', { name: 'Criar conta' }).click();
  await expect(page.getByText('Configure seu perfil')).toBeVisible();
  await expect(page.getByLabel('Nome')).toHaveValue(nome);
});
