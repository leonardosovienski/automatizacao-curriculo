"""Fila durável no banco; reserva atômica e prazo máximo por execução."""

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select, update

from .database import BuscaDB, SessionLocal


def prazo_segundos() -> int:
    return int(os.environ.get("TRIAGEM_JOB_TIMEOUT_SECONDS", "1800"))


def expirar_abandonadas() -> int:
    agora = datetime.now(timezone.utc)
    with SessionLocal() as db:
        resultado = db.execute(update(BuscaDB).where(
            BuscaDB.estado == "processando", BuscaDB.expira_em < agora,
        ).values(
            estado="falhou", progresso=100, concluida_em=agora,
            erro="O processamento excedeu o prazo. Inicie uma nova busca.",
            mensagem="Busca interrompida; resultados já salvos foram preservados.",
            worker_token=None,
        ))
        db.commit()
        return resultado.rowcount


def reservar_proxima(busca_id: str | None = None) -> tuple[str, str] | None:
    """SKIP LOCKED no PostgreSQL; update condicional também protege SQLite."""
    agora = datetime.now(timezone.utc)
    with SessionLocal() as db:
        consulta = select(BuscaDB.id).where(BuscaDB.estado == "pendente")
        if busca_id:
            consulta = consulta.where(BuscaDB.id == busca_id)
        identificador = db.scalar(consulta.order_by(BuscaDB.criada_em).limit(1)
                                  .with_for_update(skip_locked=True))
        if not identificador:
            return None
        token = str(uuid4())
        resultado = db.execute(update(BuscaDB).where(
            BuscaDB.id == identificador, BuscaDB.estado == "pendente",
        ).values(estado="processando", worker_token=token, iniciada_em=agora,
                 expira_em=agora + timedelta(seconds=prazo_segundos()),
                 progresso=1, mensagem="Preparando sua busca."))
        db.commit()
        return (identificador, token) if resultado.rowcount == 1 else None


def atualizar(busca_id: str, token: str, **campos) -> bool:
    with SessionLocal() as db:
        resultado = db.execute(update(BuscaDB).where(
            BuscaDB.id == busca_id, BuscaDB.worker_token == token,
            BuscaDB.estado == "processando",
            BuscaDB.expira_em > datetime.now(timezone.utc),
        ).values(**campos))
        db.commit()
        return resultado.rowcount == 1
