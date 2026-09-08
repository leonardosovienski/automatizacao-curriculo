"""Fila persistente e pipeline SaaS com provedores substituídos, sem rede."""

import logging
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.orm import sessionmaker

from api import processamento, queue, worker
from api.database import Base, BuscaDB, PerfilDB, Usuario, VagaDB
from triagem import alvos_ats, cache, perfil_usuario, replay
from triagem.schema import AnaliseVaga, Dimensao, Notas, VagaEncontrada


@pytest.fixture
def banco(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'worker.db').as_posix()}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def integridade(conexao, _):
        conexao.execute("PRAGMA foreign_keys=ON")
        conexao.execute("PRAGMA busy_timeout=10000")

    Base.metadata.create_all(engine)
    fabrica = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(queue, "SessionLocal", fabrica)
    monkeypatch.setattr(processamento, "SessionLocal", fabrica)
    monkeypatch.setenv("GEMINI_API_KEY", "chave-falsa-sem-rede")
    monkeypatch.setenv("TRIAGEM_JOB_TIMEOUT_SECONDS", "60")
    monkeypatch.setattr(processamento.credenciais, "carregar_no_ambiente", lambda: None)
    monkeypatch.setattr(cache, "ARQUIVO", tmp_path / "cache-operador.json")
    monkeypatch.setattr(alvos_ats, "ARQUIVO", tmp_path / "ats-operador.json")
    monkeypatch.setattr(replay, "ATIVO", True)
    yield fabrica
    engine.dispose()


def preparar(fabrica, nome="Ana", *, cv=None, consentimento=True, tipo="busca", vaga_alvo_id=None):
    with fabrica() as db:
        usuario = Usuario(email=f"{nome.lower()}@example.com", senha_hash="nao-utilizada")
        db.add(usuario)
        db.flush()
        perfil = perfil_usuario.PerfilUsuario(nome=nome, consentimento_ia=consentimento, onboarding_concluido=True)
        db.add(PerfilDB(usuario_id=usuario.id, dados=perfil.model_dump(), cv_base=cv or f"Experiência pública de {nome} com Python.\n<!-- PRIVADO -->SEGREDO-{nome}<!-- /PRIVADO -->"))
        busca = BuscaDB(usuario_id=usuario.id, pedido="Python remoto", limite=1, tipo=tipo, vaga_alvo_id=vaga_alvo_id)
        db.add(busca)
        db.commit()
        return usuario.id, busca.id


def vaga():
    return VagaEncontrada(titulo="Python Júnior", empresa="ACME", descricao="Vaga remota no Brasil para desenvolvimento Python Júnior.", link="https://acme.test/vagas/123")


def analise():
    return AnaliseVaga(
        titulo_normalizado="Python Júnior", empresa="ACME", regime="remoto", localizacao="Brasil", nivel_real="jr",
        stack_exigida=["Python"], stack_desejavel=[], idioma_trabalho="pt", link="https://acme.test/vagas/123", origem="teste",
        descartada=False, motivo_descarte=None, alertas=[],
        notas=Notas(**{nome: Dimensao(nota=8, justificativa="Evidência pública.") for nome in perfil_usuario.PerfilUsuario().pesos}),
    )


def test_reserva_concorrente_e_lease_nao_reutiliza_job(banco):
    _, busca_id = preparar(banco)
    with ThreadPoolExecutor(max_workers=8) as executor:
        reservas = list(executor.map(lambda _: queue.reservar_proxima(), range(16)))
    validas = [reserva for reserva in reservas if reserva]
    assert len(validas) == 1
    identificador, token = validas[0]
    assert identificador == busca_id
    assert not queue.atualizar(busca_id, "token-incorreto", progresso=50)
    assert queue.atualizar(busca_id, token, progresso=50)
    with banco() as db:
        registro = db.get(BuscaDB, busca_id)
        assert registro.estado == "processando"
        assert registro.progresso == 50
        registro.expira_em = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    assert not queue.atualizar(busca_id, token, estado="concluida")
    assert queue.expirar_abandonadas() == 1
    assert queue.expirar_abandonadas() == 0
    assert not queue.atualizar(busca_id, token, estado="concluida")
    with banco() as db:
        registro = db.get(BuscaDB, busca_id)
        assert registro.estado == "falhou"
        assert registro.worker_token is None
        assert registro.concluida_em


def test_pipeline_isola_cv_perfil_cache_ats_e_resultados(banco, monkeypatch):
    ana, busca_ana = preparar(banco)
    bia, busca_bia = preparar(banco, "Bia")
    perfil_original = perfil_usuario.atual()
    cache_original, ats_original = cache.ARQUIVO, alvos_ats.ARQUIVO
    chamadas, temporarios, fechados = [], [], []
    monkeypatch.setattr(processamento, "criar_cliente", lambda: SimpleNamespace(close=lambda: fechados.append(True)))

    def buscar(_cliente, cv, *_args, **_kwargs):
        nome = perfil_usuario.atual().nome
        assert nome in cv and "SEGREDO" not in cv
        assert not replay.ATIVO
        assert cache.ARQUIVO != cache_original
        assert alvos_ats.ARQUIVO != ats_original
        assert nome in processamento.system_prompt()
        temporarios.append(cache.ARQUIVO.parent)
        alvos_ats.registrar("greenhouse", "empresa-publica")
        return [vaga(), vaga()]

    def analisar(_cliente, texto, _modelo, *, cv_base):
        chamadas.append(cv_base)
        assert "SEGREDO" not in texto + cv_base
        return analise()

    monkeypatch.setattr(processamento, "buscar_vagas", buscar)
    monkeypatch.setattr(processamento, "analisar_vaga", analisar)
    processamento.executar_busca(busca_ana)
    processamento.executar_busca(busca_bia)
    assert len(chamadas) == 2  # cada job tem limite=1 mesmo com duas descobertas
    with banco() as db:
        for usuario_id, busca_id in ((ana, busca_ana), (bia, busca_bia)):
            assert db.get(BuscaDB, busca_id).estado == "concluida"
            assert db.get(BuscaDB, busca_id).encontradas == 1
            assert len(db.scalars(select(VagaDB).where(VagaDB.usuario_id == usuario_id)).all()) == 1
    assert "Bia" not in chamadas[0] and "Ana" not in chamadas[1]
    assert perfil_usuario.atual() is perfil_original
    assert cache.ARQUIVO == cache_original and alvos_ats.ARQUIVO == ats_original
    assert replay.ATIVO
    assert all(not caminho.exists() for caminho in temporarios)
    assert not ats_original.exists()
    assert len(fechados) == 2


@pytest.mark.parametrize("cv,consentimento", [
    ("Experiência pública.\n<!-- PRIVADO -->SEGREDO", True),
    ("<!-- PRIVADO -->SEGREDO<!-- /PRIVADO -->", True),
    ("Experiência pública.", False),
])
def test_privacidade_falha_antes_de_criar_cliente(banco, monkeypatch, cv, consentimento):
    _, busca_id = preparar(banco, cv=cv, consentimento=consentimento)
    clientes = []
    monkeypatch.setattr(processamento, "criar_cliente", lambda: clientes.append(True))
    processamento.executar_busca(busca_id)
    assert not clientes
    with banco() as db:
        registro = db.get(BuscaDB, busca_id)
        assert registro.estado == "falhou"
        assert "SEGREDO" not in registro.erro


def test_erro_provedor_sanitizado_e_busca_vazia(banco, monkeypatch, caplog):
    _, busca_id = preparar(banco)
    fechados = []
    monkeypatch.setattr(processamento, "criar_cliente", lambda: SimpleNamespace(close=lambda: fechados.append(True)))

    def falhar(*_, **__):
        raise ValueError("CV-INTEIRO-SEGREDO token=chave-gemini")

    monkeypatch.setattr(processamento, "buscar_vagas", falhar)
    with caplog.at_level(logging.ERROR):
        processamento.executar_busca(busca_id)
    assert "CV-INTEIRO-SEGREDO" not in caplog.text
    assert "chave-gemini" not in caplog.text
    with banco() as db:
        assert "SEGREDO" not in db.get(BuscaDB, busca_id).erro
        assert db.get(BuscaDB, busca_id).estado == "falhou"
    _, vazia_id = preparar(banco, "Bia")
    monkeypatch.setattr(processamento, "buscar_vagas", lambda *_, **__: [])
    processamento.executar_busca(vazia_id)
    with banco() as db:
        registro = db.get(BuscaDB, vazia_id)
        assert registro.estado == "concluida" and registro.encontradas == 0
    assert len(fechados) == 2


def test_revogacao_durante_busca_impede_analise_e_persistencia(banco, monkeypatch):
    usuario_id, busca_id = preparar(banco)
    monkeypatch.setattr(processamento, "criar_cliente", lambda: SimpleNamespace(close=lambda: None))
    chamadas = []

    def revogar(*_, **__):
        with banco() as db:
            db.execute(update(BuscaDB).where(BuscaDB.id == busca_id).values(estado="falhou", worker_token=None))
            perfil = db.get(PerfilDB, usuario_id)
            perfil.dados = {**perfil.dados, "consentimento_ia": False}
            db.commit()
        return [vaga()]

    monkeypatch.setattr(processamento, "buscar_vagas", revogar)
    monkeypatch.setattr(processamento, "analisar_vaga", lambda *_, **__: chamadas.append(True))
    processamento.executar_busca(busca_id)
    assert not chamadas
    with banco() as db:
        assert not db.scalars(select(VagaDB)).all()
        assert db.get(BuscaDB, busca_id).estado == "falhou"


def test_material_usa_somente_vaga_e_cv_do_dono(banco, monkeypatch):
    _, busca_id = preparar(banco, tipo="material", vaga_alvo_id="vaga-bia")
    bia, _ = preparar(banco, "Bia")
    with banco() as db:
        db.add(VagaDB(usuario_id=bia, vaga_id="vaga-bia", texto="Vaga privada Bia", analise={}))
        db.commit()
    monkeypatch.setattr(processamento, "criar_cliente", lambda: SimpleNamespace(close=lambda: None))
    chamadas = []
    monkeypatch.setattr(processamento, "gerar_material", lambda *args: chamadas.append(args) or "material")
    processamento.executar_busca(busca_id)
    assert not chamadas
    with banco() as db:
        assert db.get(BuscaDB, busca_id).estado == "falhou"
    carla, carla_id = preparar(banco, "Carla", tipo="material", vaga_alvo_id="vaga-carla")
    with banco() as db:
        db.add(VagaDB(usuario_id=carla, vaga_id="vaga-carla", texto="Vaga Carla", analise={"empresa": "ACME"}))
        db.commit()
    processamento.executar_busca(carla_id)
    assert len(chamadas) == 1 and "Carla" in chamadas[0][1] and "SEGREDO" not in chamadas[0][1]
    assert chamadas[0][2] == "Vaga Carla"
    with banco() as db:
        registro = db.get(BuscaDB, carla_id)
        assert registro.resultado == "material" and registro.estado == "concluida"


@pytest.mark.parametrize("modo", ["timeout", "crash"])
def test_worker_interrompido_libera_job_e_nao_registra_cv(banco, monkeypatch, caplog, modo):
    _, busca_id = preparar(banco)
    chamadas = []

    def executar(comando, **kwargs):
        chamadas.append((comando, kwargs))
        if modo == "timeout":
            raise subprocess.TimeoutExpired(comando, 60, stderr=b"CV-SEGREDO")
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(worker.subprocess, "run", executar)
    with caplog.at_level(logging.ERROR):
        assert worker.processar_uma()
    assert "CV-SEGREDO" not in caplog.text
    assert chamadas[0][1]["stdout"] == subprocess.DEVNULL
    assert chamadas[0][1]["stderr"] == subprocess.DEVNULL
    assert chamadas[0][1]["timeout"] == 60
    with banco() as db:
        registro = db.get(BuscaDB, busca_id)
        assert registro.estado == "falhou" and registro.concluida_em
    assert not worker.processar_uma()


def test_worker_embutido_desativavel_e_loop_sobrevive_banco_indisponivel(monkeypatch, caplog):
    monkeypatch.setenv("TRIAGEM_WORKER_MODE", "external")
    assert worker.iniciar_embutido() is None
    parar = threading.Event()

    def falhar():
        raise OSError("senha-do-banco-nao-pode-vazar")

    monkeypatch.setattr(worker, "processar_uma", falhar)
    monkeypatch.setattr(worker.ACORDAR, "wait", lambda _: parar.set())
    with caplog.at_level(logging.ERROR):
        worker.rodar(parar)
    assert "Fila indisponível" in caplog.text
    assert "senha-do-banco" not in caplog.text
