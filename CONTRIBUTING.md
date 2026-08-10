# Contributing to Triagem de Vagas

Obrigado por querer contribuir! Siga estes passos para desenvolver localmente e enviar PRs.

## Ambiente de desenvolvimento (backend)

1. Criar virtualenv e ativar

```bash
python -m venv .venv
source .venv/bin/activate   # macOS / Linux
.venv\Scripts\Activate.ps1 # Windows PowerShell
```

2. Instalar dependências de desenvolvimento

```bash
pip install -e ".[dev]"
```

3. Rodar testes

```bash
python -m pytest
```

4. Lint

```bash
python -m ruff check .
```

5. Rodar API localmente

```bash
export TRIAGEM_JWT_SECRET="teste"
python -m uvicorn api.app:app --reload
```

## Frontend

```bash
cd frontend
npm install
npm run dev
```

## Como submeter PR

- Abra uma branch com um nome descritivo: `feat/<descrição>` ou `fix/<descrição>`
- Garanta que os testes passem localmente
- Adicione um changelog entry em CHANGELOG.md se apropriado
- Faça um PR descrevendo: propósito, como testar, se precisa de secrets

## Estilo de commits

- Use mensagens no estilo Conventional Commits (ex: `feat:`, `fix:`, `chore:`)


---

Se tiver dúvidas, abra uma issue antes de implementar uma mudança maior.
