# Testes E2E (Playwright)

Suíte de ponta a ponta contra a aplicação real (API + front), cobrindo carregamento,
filtros, busca, ordenação, expansão de vaga, troca/persistência de status, o modal de
ajuda, rota inexistente, ausência de erros de console/JS/HTTP 5xx, acessibilidade (Axe) e
responsividade — em desktop e mobile.

## 1. Instalar dependências

Na raiz do `frontend/` (dependências já estão em `package.json`):

```powershell
npm install
npx playwright install chromium
```

## 2. Rodar

A API (`uvicorn api.app:app --port 8000`) precisa estar rodando e o `historico.json`
precisa ter pelo menos uma vaga (`triar analisar`/`triar buscar`) — a suíte lê o estado
real da aplicação, não usa fixtures/mocks. O Vite é subido automaticamente pelo Playwright.

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
  (best-effort), mas roda contra o `historico.json` real — evite rodar em paralelo com
  uso manual da CLI/UI.
- `E2E_SKIP_WEBSERVER=1` pula o auto-start do Vite (útil se ele já estiver rodando).
- `E2E_BASE_URL` sobrescreve a URL base (padrão `http://127.0.0.1:5173`).

## 5. CI

```powershell
npm ci
npx playwright install --with-deps chromium
npm run test:e2e
```

Retries e limite de workers em CI já vêm configurados em `playwright.config.ts`.
