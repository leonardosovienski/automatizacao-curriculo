# Testes E2E (Playwright)

Suíte de ponta a ponta contra a aplicação real (API + front), cobrindo carregamento,
filtros, busca, ordenação, expansão de vaga, troca/persistência de status, o modal de
ajuda, rota inexistente, ausência de erros de console/JS/HTTP 5xx, acessibilidade (Axe) e
responsividade — em desktop e mobile.

Estado validado na `main` em 2026-08-08: **42/42 testes aprovados** localmente e no gate
E2E do GitHub Actions.

**A suíte é auto-contida**: o Playwright sobe API e Vite sozinho, com um
`historico.json` isolado e populado a partir de um fixture estático (ver
`api/seed_e2e.py` e `tests/e2e/fixtures/historico.seed.json`) — não toca no
`historico.json` real do usuário nem faz chamadas ao Gemini.

## 1. Instalar dependências

Na raiz do `frontend/` (dependências já estão em `package.json`):

```powershell
npm install
npx playwright install chromium
```

O projeto Python (`pip install -e ".[dev]"`, na raiz do repo) precisa estar instalado —
é ele que sobe a API durante o teste.

## 2. Rodar

```powershell
npm run test:e2e
```

Para ver o navegador rodando:

```powershell
npm run test:e2e:headed
```

Para depurar interativamente (recomendado na primeira rodada):

```powershell
npm run test:e2e:ui
```

## 3. Artefatos em falha

Screenshot, vídeo e trace são salvos automaticamente em `test-results/` quando um teste
falha. Relatório HTML:

```powershell
npm run test:e2e:report
```

Trace específico:

```powershell
npx playwright show-trace test-results\<pasta-do-teste>\trace.zip
```

## 4. Observações

- A suíte tenta restaurar o status original das vagas depois de alguns testes
  (best-effort), mas isso é sobre o `.e2e-historico.json` isolado — nunca toca no
  `historico.json` real.
- `E2E_SKIP_WEBSERVER=1` pula o auto-start de API e Vite (útil se ambos já estiverem
  no ar — nesse caso a API precisa apontar para o mesmo `historico.json` que os testes
  esperam).
- `E2E_BASE_URL` sobrescreve a URL base do front (padrão `http://127.0.0.1:5173`).
- `E2E_PYTHON` sobrescreve o binário Python usado para subir a API (padrão `python`;
  em alguns sistemas Linux/Mac pode ser necessário `python3`).

## 5. CI

```powershell
npm ci
npx playwright install --with-deps chromium
npm run test:e2e
```

Retries e limite de workers em CI já vêm configurados em `playwright.config.ts`.
