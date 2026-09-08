import { expect, test, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const config = { nome_servico: 'Triagem de Vagas', suporte_email: 'suporte@example.com', termos_url: 'https://example.com/termos', privacidade_url: 'https://example.com/privacidade' };
const usuario = { id: 'cliente-1', email: 'cliente@example.com' };
const perfil = { versao: 1, nome: 'Ana', pais: 'Brasil', cidades_aceitas: ['Curitiba'], aceita_remoto: true, aceita_hibrido: false, aceita_presencial: false, areas: ['QA'], senioridades: ['Júnior'], tecnologias: [], idiomas: ['Português'], pesos: { d1_crescimento: .3, d2_regime_localizacao: .25, d3_stack_fit: .2, d4_ingles: .15, d5_nivel_real: .1 }, cv_base: '', consentimento_ia: true, onboarding_concluido: true };
const assinatura = { status: 'inativa', ativa: false, periodo_atual_fim: null, configurado: true, plano: { nome: 'Profissional', valor_centavos: 4990, moeda: 'brl', intervalo: 'month', intervalo_contagem: 1 }, uso: { mes: '2026-09', buscas_utilizadas: 2, buscas_limite: 30, analises_reservadas: 20, analises_limite: 300, reinicia_em: '2026-10-01T00:00:00Z' } };
const vaga = { id: 'vaga-1', titulo: 'Desenvolvedor Python', empresa: 'Acme', status: 'novo', score_final: 85, regime: 'remoto', localizacao: 'Brasil', nivel_real: 'jr', idioma_trabalho: 'pt', analisado_em: '2026-09-08T00:00:00Z', link: 'https://example.com/vaga', stack_exigida: ['Python'], stack_desejavel: [], alertas: [], motivo_descarte: null, notas: null };
async function mockApp(page: Page, logado = true) {
  await page.route('**/api/**', async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === '/api/config/publico') return route.fulfill({ json: config });
    if (pathname === '/api/auth/me') return route.fulfill(logado ? { json: usuario } : { status: 401, json: { detail: 'Faça login.' } });
    if (pathname === '/api/onboarding') return route.fulfill({ json: { concluido: true, consentimento_ia: true, cv_configurado: true } });
    if (pathname === '/api/perfil') return route.fulfill({ json: perfil });
    if (pathname === '/api/cv') return route.fulfill({ json: { conteudo: 'Experiência de três anos com automação de testes em Python e qualidade de software.' } });
    if (pathname === '/api/stats') return route.fulfill({ json: { total: 1, por_status: { novo: 1, aplicado: 0, entrevista: 0, fechada: 0 } } });
    if (pathname === '/api/vagas') return route.fulfill({ json: [vaga] });
    if (pathname === '/api/buscas/atual') return route.fulfill({ json: null });
    return route.fulfill({ status: 404, json: { detail: 'Não encontrado.' } });
  });
  await page.route('**/billing/status', route => route.fulfill({ json: assinatura }));
}

test('cadastro exige confirmação e aceite e envia contrato completo', async ({ page }) => {
  await mockApp(page, false);
  let payload: Record<string, unknown> | undefined;
  await page.route('**/api/auth/cadastro', async route => {
    payload = route.request().postDataJSON();
    await route.fulfill({ status: 422, json: { detail: [{ msg: 'invalid input' }] } });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Criar uma conta' }).click();
  await page.getByLabel('E-mail', { exact: true }).fill('nova@example.com');
  await page.getByLabel('Senha', { exact: true }).fill('senha-segura-123');
  await page.getByLabel('Confirmar senha').fill('senha-diferente-123');
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Criar conta', exact: true }).click();
  await expect(page.getByRole('alert')).toHaveText('As senhas precisam ser iguais.');
  expect(payload).toBeUndefined();
  await page.getByLabel('Confirmar senha').fill('senha-segura-123');
  await page.getByRole('button', { name: 'Criar conta', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Não foi possível concluir');
  expect(payload).toEqual({ email: 'nova@example.com', senha: 'senha-segura-123', aceite_termos: true });
  await expect(page.getByRole('link', { name: 'termos de uso', exact: true }).first()).toHaveAttribute('href', config.termos_url);
});

test('recuperação responde sem revelar cadastro e remove token do endereço', async ({ page }) => {
  await mockApp(page, false);
  await page.route('**/api/auth/recuperar-senha', route => route.fulfill({ status: 202, json: { mensagem: 'Se este e-mail estiver cadastrado, você receberá um link.' } }));
  await page.goto('/');
  await page.getByRole('button', { name: 'Esqueci minha senha' }).click();
  await page.getByLabel('E-mail').fill('cliente@example.com');
  await page.getByRole('button', { name: 'Enviar link de recuperação' }).click();
  await expect(page.getByRole('status')).toContainText('Se este e-mail estiver cadastrado');
  let enviado: unknown;
  await page.route('**/api/auth/redefinir-senha', route => { enviado = route.request().postDataJSON(); return route.fulfill({ json: { mensagem: 'Senha atualizada.' } }); });
  await page.goto('/redefinir-senha#token=segredo-temporario');
  await expect(page.getByRole('heading', { name: 'Defina sua nova senha' })).toBeVisible();
  expect(page.url()).not.toContain('segredo-temporario');
  await page.getByLabel('Nova senha', { exact: true }).fill('senha-atualizada-123');
  await page.getByLabel('Confirmar senha').fill('senha-atualizada-123');
  await page.getByRole('button', { name: 'Salvar nova senha' }).click();
  await expect(page.getByRole('heading', { name: 'Entre na sua conta' })).toBeVisible();
  expect(enviado).toEqual({ token: 'segredo-temporario', senha: 'senha-atualizada-123' });
});

test('retorno checkout confirma via servidor e exibe preço e cotas reais', async ({ page }) => {
  await mockApp(page);
  let sincronizacoes = 0;
  await page.route('**/billing/sincronizar', route => { sincronizacoes += 1; return route.fulfill({ json: { ...assinatura, status: 'active', ativa: true } }); });
  await page.goto('/?sucesso=1');
  const modal = page.getByRole('dialog', { name: 'Seu plano e assinatura' });
  await expect(modal.getByRole('status')).toContainText('Assinatura confirmada');
  await expect(modal).toContainText('49,90');
  await expect(modal).toContainText('2 / 30');
  await expect(modal.getByRole('button', { name: 'Gerenciar assinatura' })).toBeVisible();
  expect(sincronizacoes).toBeGreaterThan(0);
  expect(page.url()).not.toContain('sucesso=1');
});

test('retorno checkout pendente não declara sucesso nem oferece segunda cobrança', async ({ page }) => {
  await mockApp(page);
  await page.route('**/billing/sincronizar', route => route.fulfill({ json: assinatura }));
  await page.goto('/?sucesso=1');
  const modal = page.getByRole('dialog');
  await expect(modal.getByRole('status')).toContainText('Confirmando seu pagamento');
  await expect(modal.getByRole('button', { name: 'Assinar agora' })).toHaveCount(0);
  await expect(modal).not.toContainText('Assinatura confirmada');
});

test('erro de plano abre assinatura e renovação da sessão volta ao login', async ({ page }) => {
  await mockApp(page);
  await page.route('**/api/buscas', route => route.fulfill({ status: 402, json: { detail: 'Assine para buscar vagas.' } }));
  await page.goto('/');
  await page.getByRole('button', { name: 'Buscar vagas', exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'Seu plano e assinatura' })).toBeVisible();
  await page.getByRole('dialog').getByRole('button', { name: 'Fechar', exact: true }).click();
  await page.route('**/api/vagas*', route => route.fulfill({ status: 401, json: { detail: 'Sessão expirada.' } }));
  await page.getByRole('button', { name: 'Todas', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Entre na sua conta' })).toBeVisible();
  await expect(page.getByRole('status')).toContainText('Sua sessão expirou');
});

test('conta exporta dados e exclusão exige confirmação e senha', async ({ page }) => {
  await mockApp(page);
  let excluido = false;
  await page.route('**/api/auth/exportar', route => route.fulfill({ json: { usuario, cv_base: 'Meu currículo' } }));
  await page.route('**/api/auth/me', route => {
    if (route.request().method() === 'DELETE') { expect(route.request().postDataJSON()).toEqual({ senha: 'senha-segura-123' }); excluido = true; return route.fulfill({ status: 204 }); }
    return route.fulfill({ json: usuario });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Minha conta e privacidade', exact: true }).click();
  const modal = page.getByRole('dialog');
  await expect(modal.getByRole('button', { name: 'Excluir minha conta definitivamente' })).toBeDisabled();
  const download = page.waitForEvent('download');
  await modal.getByRole('button', { name: 'Exportar meus dados' }).click();
  expect((await download).suggestedFilename()).toMatch(/^meus-dados-.*\.json$/);
  await modal.getByLabel('Confirme sua senha').fill('senha-segura-123');
  await modal.getByRole('checkbox').check();
  await modal.getByRole('button', { name: 'Excluir minha conta definitivamente' }).click();
  await expect(page.getByRole('heading', { name: 'Entre na sua conta' })).toBeVisible();
  await expect(page.getByRole('status')).toContainText('Sua conta foi excluída');
  expect(excluido).toBeTruthy();
});

test('perfil preserva vírgulas durante digitação e aceita retirada de consentimento', async ({ page }) => {
  await mockApp(page);
  let salvo: Record<string, unknown> | undefined;
  await page.route('**/api/perfil', route => {
    if (route.request().method() === 'PUT') salvo = route.request().postDataJSON();
    return route.fulfill({ json: salvo || perfil });
  });
  await page.route('**/api/cv', route => route.fulfill({ json: { conteudo: 'Experiência profissional com Python e automação de testes em projetos de software.' } }));
  await page.goto('/');
  await page.getByRole('button', { name: 'Configurações do perfil' }).click();
  const campo = page.getByLabel('Áreas/cargos, separados por vírgula');
  await campo.clear(); await campo.pressSequentially('QA, Automação');
  await expect(campo).toHaveValue('QA, Automação');
  await page.getByRole('checkbox', { name: /Autorizo o envio/ }).uncheck();
  await page.getByRole('button', { name: 'Concluir configuração' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(salvo?.areas).toEqual(['QA', 'Automação']);
  expect(salvo?.consentimento_ia).toBe(false);
});

test('material de candidatura acompanha fila e permite download para revisão', async ({ page }) => {
  await mockApp(page);
  const job = { id: 'material-1', tipo: 'material', vaga_alvo_id: vaga.id, pedido: '', limite: 1, erro: null, encontradas: 0, criada_em: '2026-09-08T00:00:00Z', concluida_em: null, resultado: null, estado: 'pendente', progresso: 0, mensagem: 'Preparando material.' };
  await page.route('**/api/vagas/vaga-1/material', route => route.fulfill({ status: route.request().method() === 'POST' ? 202 : 200, json: route.request().method() === 'POST' ? job : null }));
  await page.route('**/api/buscas/material-1', route => route.fulfill({ json: { ...job, estado: 'concluida', progresso: 100, resultado: '# Currículo adaptado\nExperiência com Python.', mensagem: 'Material pronto para revisão.' } }));
  await page.goto('/');
  await page.getByRole('button', { name: 'Preparar candidatura' }).click();
  await page.getByRole('button', { name: 'Gerar material (2 análises)' }).click();
  await expect(page.getByLabel('Material para revisar')).toHaveValue(/Currículo adaptado/, { timeout: 7000 });
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Baixar material (.md)' }).click();
  expect((await download).suggestedFilename()).toBe('candidatura-vaga-1.md');
});

test('modal mantém foco, fecha com Escape e login passa acessibilidade', async ({ page }) => {
  await mockApp(page);
  await page.goto('/');
  const trigger = page.getByRole('button', { name: 'Minha conta e privacidade', exact: true });
  await trigger.click();
  const modal = page.getByRole('dialog');
  await expect(modal).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(modal.getByRole('link', { name: 'Falar com suporte' })).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(modal.getByRole('button', { name: 'Fechar', exact: true })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(trigger).toBeFocused();
  await mockApp(page, false);
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Entre na sua conta' })).toBeVisible();
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.filter((v) => ['critical', 'serious'].includes(v.impact || ''))).toEqual([]);
});
