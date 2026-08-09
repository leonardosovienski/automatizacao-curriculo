import { expect, test } from '@playwright/test';

const perfil = {
  versao: 1, nome: 'Candidato', pais: 'Brasil', cidades_aceitas: ['Curitiba'],
  aceita_remoto: true, aceita_hibrido: true, aceita_presencial: false,
  areas: ['DevOps'], senioridades: ['Júnior'], tecnologias: [],
  idiomas: ['Português', 'Inglês'],
  pesos: { d1_crescimento: .3, d2_regime_localizacao: .25, d3_stack_fit: .2, d4_ingles: .15, d5_nivel_real: .1 },
  cv_base: 'perfil/cv_base.md', consentimento_ia: false, onboarding_concluido: false,
};

test('onboarding obrigatório salva perfil e CV sem devolver credenciais', async ({ page }) => {
  const gravados: string[] = [];
  await page.route('**/api/**', async (route) => {
    const url = route.request().url();
    if (url.endsWith('/api/auth/me')) return route.fulfill({ json: { id: '1', email: 'teste@example.com' } });
    if (url.endsWith('/api/onboarding')) return route.fulfill({ json: { concluido: false, consentimento_ia: false, cv_configurado: false, credenciais: {} } });
    if (url.endsWith('/api/perfil') && route.request().method() === 'GET') return route.fulfill({ json: perfil });
    if (url.endsWith('/api/cv') && route.request().method() === 'GET') return route.fulfill({ json: { conteudo: '', caminho: 'cv.md' } });
    if (route.request().method() === 'PUT') {
      gravados.push(url);
      return route.fulfill({ json: url.endsWith('/perfil') ? { ...perfil, onboarding_concluido: true } : { salvo: true } });
    }
    if (url.endsWith('/api/stats')) return route.fulfill({ json: { total: 0, por_status: {} } });
    if (url.includes('/api/vagas')) return route.fulfill({ json: [] });
    return route.fulfill({ status: 404 });
  });
  await page.goto('/');
  await expect(page.getByText('Configure seu perfil')).toBeVisible();
  await page.getByLabel('Nome').fill('Ana');
  await page.getByLabel('Áreas/cargos, separados por vírgula').fill('QA, Automação');
  await page.getByRole('button', { name: 'Concluir configuração' }).click();
  await expect.poll(() => gravados.some((u) => u.endsWith('/api/cv'))).toBe(true);
  await expect.poll(() => gravados.some((u) => u.endsWith('/api/perfil'))).toBe(true);
  await expect(page.getByText('Configure seu perfil')).toBeHidden();
});
