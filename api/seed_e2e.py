"""Sobe a API com banco multiusuário isolado para Playwright."""

import json
import os
import sys
from pathlib import Path

_FIXTURE = Path(__file__).resolve().parent.parent / "frontend/tests/e2e/fixtures/historico.seed.json"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    caminho = Path(os.environ["TRIAGEM_DATABASE"])
    if caminho.exists():
        caminho.unlink()

    from api.auth import hash_senha
    from api.database import PerfilDB, SessionLocal, Usuario, VagaDB, criar_tabelas
    from triagem.perfil_usuario import PerfilUsuario

    criar_tabelas()
    fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    with SessionLocal() as db:
        usuario = Usuario(email="e2e@example.com", senha_hash=hash_senha("senha-e2e-123"))
        db.add(usuario)
        db.flush()
        perfil = PerfilUsuario(
            nome="E2E", cidades_aceitas=["Curitiba"], areas=["DevOps"],
            senioridades=["Júnior"], onboarding_concluido=True,
        )
        db.add(PerfilDB(usuario_id=usuario.id, dados=perfil.model_dump(), cv_base="# CV E2E"))
        for vaga_id, entrada in fixture.items():
            db.add(VagaDB(
                usuario_id=usuario.id, vaga_id=vaga_id, status=entrada["status"],
                score_final=entrada.get("score_final"), analisado_em=entrada.get("analisado_em", ""),
                texto=entrada.get("texto", ""), analise=entrada.get("analise", {}),
                aliases=entrada.get("aliases", []),
            ))
        db.commit()

    porta = os.environ.get("E2E_API_PORT", "8000")
    os.execvp(sys.executable, [sys.executable, "-m", "uvicorn", "api.app:app", "--port", porta])


if __name__ == "__main__":
    main()
