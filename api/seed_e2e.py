"""Sobe a API com um historico.json isolado, populado a partir de um fixture estático.

Uso exclusivo dos testes E2E (Playwright, via webServer em playwright.config.ts) — nunca
é chamado pela CLI nem em produção. Existe para que o E2E não dependa do historico.json
real do usuário nem faça chamadas ao Gemini: os dados já vêm prontos no fixture.
"""

import os
import sys
from pathlib import Path

_FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "frontend"
    / "tests"
    / "e2e"
    / "fixtures"
    / "historico.seed.json"
)


def main() -> None:
    destino = Path(os.environ["TRIAGEM_HISTORICO"])
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

    porta = os.environ.get("E2E_API_PORT", "8000")
    os.execvp(sys.executable, [sys.executable, "-m", "uvicorn", "api.app:app", "--port", porta])


if __name__ == "__main__":
    main()
