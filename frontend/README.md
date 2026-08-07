# Triagem de Vagas — Frontend

Interface web para o histórico de vagas gerado pelo `triar` (ver `../api/app.py`
para o backend). React 19 + TypeScript + Vite + Tailwind v4.

## Como rodar

```bash
# 1. Backend (na raiz do projeto)
python -m uvicorn api.app:app --port 8000

# 2. Frontend
cp .env.example .env.local   # ajuste VITE_API_URL se necessário
npm install
npm run dev
```

Abra `http://localhost:5173`. A lista mostra as vagas do `historico.json`
(geradas por `triar analisar`/`triar buscar`), com filtro por status e opção
de atualizar o status diretamente na UI — a mudança é gravada no mesmo
`historico.json` usado pela CLI.

## Scripts

- `npm run dev` — servidor de desenvolvimento
- `npm run build` — build de produção (`tsc -b && vite build`)
- `npm run lint` — oxlint
