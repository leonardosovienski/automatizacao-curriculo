# Triagem de Vagas — Frontend

Interface web para o histórico de vagas gerado pelo `triar` (ver `../api/app.py`
para o backend). React 19 + TypeScript + Vite + Tailwind v4.

## Como rodar

```bash
# 1. Backend (na raiz do projeto)
python -m uvicorn api.app:app --host 127.0.0.1 --port 8000

# 2. Frontend
cp .env.example .env.local   # ajuste VITE_API_URL se necessário
npm install
npm run dev
```

Abra `http://localhost:5173`. A lista mostra as vagas do `historico.json`
(geradas por `triar analisar`/`triar buscar`), com filtro por status e opção
de atualizar o status diretamente na UI — a mudança é gravada no mesmo
`historico.json` usado pela CLI.

> **Somente localhost:** a API não possui autenticação e o `PATCH` altera o status das
> vagas. Não use `--host 0.0.0.0`, encaminhamento de porta ou proxy público sem adicionar
> autenticação e HTTPS na frente do serviço.

## Scripts

- `npm run dev` — servidor de desenvolvimento
- `npm run build` — build de produção (`tsc -b && vite build`)
- `npm run lint` — oxlint
- `npm run test:e2e` — suíte Playwright (desktop + mobile), gate obrigatório do CI
- `npm run test:e2e:ui` / `:headed` / `:debug` — variações interativas
- `npm run test:e2e:report` — abre o último relatório HTML

A suíte E2E é **auto-contida**: sobe API e Vite sozinha, com um `historico.json`
isolado populado de um fixture estático — não toca no seu histórico real nem
chama o Gemini. Ver [PLAYWRIGHT-SETUP.md](PLAYWRIGHT-SETUP.md).

## Contrato com a API

`src/types.ts` espelha `api/app.py`. O enum `Status` tem os mesmos seis valores de
`historico.StatusVaga` — se um lado mudar, o outro precisa mudar junto.

Os campos `regime`, `nivel_real` e `idioma_trabalho` chegam como `string` livre, e
não como união fechada, porque vêm do `historico.json` em disco, que pode ter
registros de pipelines antigos. O fallback de label (`REGIME_LABEL[x] ?? x`) existe
para isso — não é descuido.
