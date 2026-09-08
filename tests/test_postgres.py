"""Integração PostgreSQL real, opt-in e restrita a schemas descartáveis de teste.

Execute com TRIAGEM_TEST_POSTGRES_URL apontando para um banco exclusivo de testes.
DATABASE_URL nunca é usado como fallback. O usuário precisa poder criar schemas.
"""

import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier, Event
from unittest.mock import Mock
from uuid import uuid4

import pytest
import stripe
from fastapi import HTTPException, Request, Response
from sqlalchemy import create_engine, delete, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from api import billing, queue
from api.billing_models import EventoStripeDB, UsoMensalDB
from api.database import AssinaturaDB, BuscaDB, Usuario

RAIZ = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    not os.environ.get("TRIAGEM_TEST_POSTGRES_URL"),
    reason="PostgreSQL opt-in: configure TRIAGEM_TEST_POSTGRES_URL (nunca DATABASE_URL).",
)


@pytest.fixture
def postgres(monkeypatch):
    url = make_url(os.environ["TRIAGEM_TEST_POSTGRES_URL"])
    if url.get_backend_name() not in {"postgresql", "postgres"}:
        pytest.fail("TRIAGEM_TEST_POSTGRES_URL precisa apontar para PostgreSQL de testes.")
    url = url.set(drivername="postgresql+psycopg")
    schema = "triagem_test_" + uuid4().hex
    assert re.fullmatch(r"triagem_test_[0-9a-f]{32}", schema)
    administrador = create_engine(url, pool_pre_ping=True)
    with administrador.begin() as db:
        db.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolada = url.update_query_dict(
        {"options": f"-csearch_path={schema} -cstatement_timeout=15000"}
    )
    engine = create_engine(isolada, pool_size=16, max_overflow=0, pool_pre_ping=True)
    fabrica = sessionmaker(bind=engine, expire_on_commit=False)

    def migrar(acao, revisao):
        ambiente = dict(os.environ)
        ambiente.update(
            DATABASE_URL=isolada.render_as_string(hide_password=False),
            TRIAGEM_ENV="development",
            TRIAGEM_WORKER_MODE="disabled",
        )
        resultado = subprocess.run(
            [sys.executable, "-m", "alembic", acao, revisao],
            cwd=RAIZ,
            env=ambiente,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        assert resultado.returncode == 0, resultado.stdout + resultado.stderr

    monkeypatch.setenv("STRIPE_PRICE_ID", "price_postgres_test")
    monkeypatch.setenv("TRIAGEM_MONTHLY_SEARCH_LIMIT", "3")
    monkeypatch.setenv("TRIAGEM_MONTHLY_ANALYSIS_LIMIT", "30")
    monkeypatch.setattr(queue, "SessionLocal", fabrica)
    try:
        migrar("upgrade", "head")
        yield fabrica, migrar, engine
    finally:
        engine.dispose()
        # O único alvo destrutivo é o schema aleatório que este fixture criou.
        assert re.fullmatch(r"triagem_test_[0-9a-f]{32}", schema)
        with administrador.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        administrador.dispose()


def criar_usuario(fabrica, identificador="u1"):
    with fabrica() as db:
        db.add(Usuario(id=identificador, email=f"{identificador}@example.test", senha_hash="teste"))
        db.flush()
        db.add(
            AssinaturaDB(
                usuario_id=identificador,
                stripe_customer_id=f"cus_{identificador}",
                stripe_subscription_id=f"sub_{identificador}",
                preco_id="price_postgres_test",
                status="active",
                periodo_atual_fim=datetime.now(timezone.utc) + timedelta(days=30),
            )
        )
        db.commit()


def test_postgres_migracoes_downgrade_reupgrade_preservam_dados(postgres):
    fabrica, migrar, engine = postgres
    criar_usuario(fabrica)
    migrar("downgrade", "0003_assinaturas")
    assert "uso_mensal" not in inspect(engine).get_table_names()
    with engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO buscas (id, usuario_id, pedido, limite, estado, progresso, "
                "mensagem, encontradas, criada_em) VALUES "
                "('job_legado', 'u1', 'Python', 5, 'processando', 20, 'Anterior', 0, NOW())"
            )
        )
    migrar("upgrade", "head")
    with fabrica() as db:
        assert db.get(Usuario, "u1").email == "u1@example.test"
        assert db.get(AssinaturaDB, "u1").status == "active"
        assert db.get(BuscaDB, "job_legado").estado == "falhou"
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == "0006_durable_jobs"
    migrar("upgrade", "head")  # Reiniciar o deploy não reaplica migrações.


def test_postgres_quota_serializa_oito_pedidos_concorrentes(postgres):
    fabrica, _, _ = postgres
    criar_usuario(fabrica)
    barreira = Barrier(8)

    def reservar(_):
        with fabrica() as db:
            usuario = db.get(Usuario, "u1")
            barreira.wait(timeout=20)
            try:
                billing.reservar_busca(db, usuario, 1)
                db.commit()
                return 202
            except HTTPException as erro:
                db.rollback()
                return erro.status_code

    with ThreadPoolExecutor(max_workers=8) as executor:
        respostas = list(executor.map(reservar, range(8)))
    assert respostas.count(202) == 3
    assert respostas.count(429) == 5
    with fabrica() as db:
        uso = db.scalar(select(UsoMensalDB))
        assert (uso.buscas, uso.analises) == (3, 3)


def test_postgres_um_job_ativo_por_conta_e_indice_defensivo(postgres):
    fabrica, _, _ = postgres
    criar_usuario(fabrica)
    barreira = Barrier(4)

    def iniciar(_):
        with fabrica() as db:
            usuario = db.get(Usuario, "u1")
            barreira.wait(timeout=20)
            try:
                billing.reservar_busca(db, usuario, 1)
                db.add(BuscaDB(usuario_id="u1", pedido="Python", limite=1))
                db.commit()
                return 202
            except HTTPException as erro:
                db.rollback()
                return erro.status_code

    with ThreadPoolExecutor(max_workers=4) as executor:
        respostas = list(executor.map(iniciar, range(4)))
    assert sorted(respostas) == [202, 409, 409, 409]
    with fabrica() as db:
        db.add(BuscaDB(usuario_id="u1", pedido="Bypass da aplicação", limite=1))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        assert db.scalar(select(func.count()).select_from(BuscaDB)) == 1


def test_postgres_worker_skip_locked_e_fencing_token(postgres):
    fabrica, _, _ = postgres
    for identificador in ("u1", "u2"):
        criar_usuario(fabrica, identificador)
        with fabrica() as db:
            db.add(
                BuscaDB(
                    id=f"job_{identificador}", usuario_id=identificador, pedido="Python", limite=1
                )
            )
            db.commit()
    with fabrica() as travada:
        travada.execute(select(BuscaDB).where(BuscaDB.id == "job_u1").with_for_update())
        reserva = queue.reservar_proxima()
        assert reserva and reserva[0] == "job_u2"
        assert not queue.atualizar(reserva[0], "token_incorreto", progresso=50)
        assert queue.atualizar(reserva[0], reserva[1], progresso=50)
        travada.rollback()
    with ThreadPoolExecutor(max_workers=4) as executor:
        reservas = list(executor.map(lambda _: queue.reservar_proxima(), range(4)))
    assert sum(reserva is not None for reserva in reservas) == 1
    with fabrica() as db:
        job = db.get(BuscaDB, "job_u2")
        token_antigo = job.worker_token
        job.expira_em = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    assert queue.expirar_abandonadas() == 1
    assert not queue.atualizar("job_u2", token_antigo, estado="concluida")


def test_postgres_webhook_duplicado_concorrente_e_cascade_conta(postgres, monkeypatch):
    fabrica, _, _ = postgres
    criar_usuario(fabrica)
    objeto = {
        "id": "sub_u1",
        "customer": "cus_u1",
        "status": "active",
        "created": 1,
        "items": {
            "data": [
                {
                    "price": {"id": "price_postgres_test"},
                    "current_period_end": int(datetime.now(timezone.utc).timestamp()) + 86400,
                }
            ]
        },
    }
    monkeypatch.setattr(stripe.Subscription, "list", lambda **kw: {"data": [objeto]})
    evento = {
        "id": "evt_postgres",
        "type": "customer.subscription.updated",
        "data": {"object": objeto},
    }
    barreira = Barrier(4)

    def entregar(_):
        with fabrica() as db:
            barreira.wait(timeout=20)
            return billing._processar_evento(db, evento)

    with ThreadPoolExecutor(max_workers=4) as executor:
        respostas = list(executor.map(entregar, range(4)))
    assert all(resposta["recebido"] for resposta in respostas)
    with fabrica() as db:
        assert db.scalar(select(func.count()).select_from(EventoStripeDB)) == 1
        billing.reservar_busca(db, db.get(Usuario, "u1"), 1)
        db.commit()
        db.execute(delete(Usuario).where(Usuario.id == "u1"))
        db.commit()
        assert db.scalar(select(func.count()).select_from(AssinaturaDB)) == 0
        assert db.scalar(select(func.count()).select_from(UsoMensalDB)) == 0
        # Auditoria guarda apenas ID/tipo de evento, sem dados pessoais do cliente.
        assert db.scalar(select(func.count()).select_from(EventoStripeDB)) == 1


def test_postgres_exclusao_aguarda_reset_e_revalida_senha_carregada(postgres, monkeypatch):
    from api import app as api_app
    from api.auth import hash_senha, verificar_senha

    fabrica, _, _ = postgres
    criar_usuario(fabrica)
    senha_antiga = "senha-antiga-sintetica-123"
    senha_nova = "senha-nova-sintetica-456"
    with fabrica() as db:
        db.get(Usuario, "u1").senha_hash = hash_senha(senha_antiga)
        db.commit()

    cancelar = Mock()
    monkeypatch.setattr(api_app, "encerrar_cobranca_para_exclusao", cancelar)
    monkeypatch.setattr(api_app, "limitar_auth", lambda *_: None)
    usuario_carregado = Event()

    def excluir_com_identidade_anterior():
        with fabrica() as db:
            # MVCC permite autenticar com o valor ainda confirmado enquanto o reset
            # mantém a nova senha não confirmada em outra transação.
            usuario = db.get(Usuario, "u1")
            assert verificar_senha(senha_antiga, usuario.senha_hash)
            usuario_carregado.set()
            try:
                api_app.excluir_conta(
                    api_app.ExcluirContaPayload(senha=senha_antiga),
                    Request({"type": "http", "method": "DELETE", "path": "/api/auth/me"}),
                    Response(),
                    usuario,
                    db,
                )
            except HTTPException as erro:
                return erro.status_code
            return 204

    novo_hash = hash_senha(senha_nova)
    with ThreadPoolExecutor(max_workers=1) as executor:
        # O contexto interno sempre libera o lock antes de aguardar o executor,
        # inclusive se uma asserção falhar, evitando travar a própria suíte.
        with fabrica.begin() as reset:
            usuario = reset.scalar(
                select(Usuario).where(Usuario.id == "u1").with_for_update()
            )
            usuario.senha_hash = novo_hash
            reset.flush()
            exclusao = executor.submit(excluir_com_identidade_anterior)
            assert usuario_carregado.wait(10), "A exclusão não carregou a identidade anterior"
            with pytest.raises(FutureTimeoutError):
                exclusao.result(timeout=0.2)
        assert exclusao.result(timeout=10) == 403

    cancelar.assert_not_called()
    with fabrica() as db:
        usuario = db.get(Usuario, "u1")
        assert usuario is not None
        assert verificar_senha(senha_nova, usuario.senha_hash)
        assert not verificar_senha(senha_antiga, usuario.senha_hash)
        assert db.get(AssinaturaDB, "u1").status == "active"
