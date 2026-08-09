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

Abra `http://localhost:5173`, crie uma conta e conclua o perfil. A lista mostra somente as
vagas pertencentes ao usuário autenticado no banco.

> Em produção, configure PostgreSQL, `TRIAGEM_JWT_SECRET`, HTTPS e a origem CORS exata do
> frontend. As chaves das fontes e da IA existem somente no backend do operador.

## Scripts

- `npm run dev` — servidor de desenvolvimento
- `npm run build` — build de produção (`tsc -b && vite build`)
- `npm run lint` — oxlint
- `npm run test:e2e` — suíte Playwright (desktop + mobile), gate obrigatório do CI
- `npm run test:e2e:ui` / `:headed` / `:debug` — variações interativas
- `npm run test:e2e:report` — abre o último relatório HTML

A suíte E2E possui **42 testes em desktop e mobile** e é **auto-contida**: sobe API e Vite
sozinha, com um banco SQLite
isolado populado de um fixture estático — não toca no seu histórico real nem
chama o Gemini. Ver [PLAYWRIGHT-SETUP.md](PLAYWRIGHT-SETUP.md).

## Contrato com a API

`src/types.ts` espelha `api/app.py`. O enum `Status` tem os mesmos seis valores de
`historico.StatusVaga` — se um lado mudar, o outro precisa mudar junto.

Os campos `regime`, `nivel_real` e `idioma_trabalho` chegam como `string` livre, e
não como união fechada, porque vêm dos dados persistidos, que podem ter
registros de pipelines antigos. O fallback de label (`REGIME_LABEL[x] ?? x`) existe
para isso — não é descuido.
