"""Fluxos de conta, materiais e configuração usando a aplicação integrada."""

import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from api import app as api_app
from api import config
from api.auth_models import RecuperacaoSenhaDB
from api.billing_models import UsoMensalDB
from api.database import AssinaturaDB, Base, BuscaDB, PerfilDB, Usuario, VagaDB, sessao


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    monkeypatch.setenv("TRIAGEM_ENV", "development")
    monkeypatch.delenv("TRIAGEM_PUBLIC_URL", raising=False)
    monkeypatch.setenv("TRIAGEM_JWT_SECRET", "segredo-longo-teste-isolado-api-saas-123456")
    monkeypatch.setenv("GEMINI_API_KEY", "chave-falsa-sem-rede")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_test")
    engine = create_engine(f"sqlite:///{(tmp_path / 'saas.db').as_posix()}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def integridade(conexao, _):
        conexao.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    fabrica = sessionmaker(bind=engine, expire_on_commit=False)

    def db_teste():
        with fabrica() as db:
            yield db

    agendadas, canceladas = [], []
    monkeypatch.setattr(api_app, "agendar", agendadas.append)
    monkeypatch.setattr(api_app, "encerrar_cobranca_para_exclusao", lambda _db, usuario: canceladas.append(usuario.id))
    api_app.app.dependency_overrides[sessao] = db_teste
    cliente = TestClient(api_app.app, headers={"Origin": "http://testserver"})
    try:
        yield cliente, fabrica, agendadas, canceladas
    finally:
        cliente.close()
        api_app.app.dependency_overrides.clear()
        engine.dispose()


def cadastrar(cliente, nome="ana"):
    resposta = cliente.post("/api/auth/cadastro", json={"email": f"{nome}@example.com", "senha": "senha-forte-123", "aceite_termos": True})
    assert resposta.status_code == 201, resposta.text
    return resposta.json()["usuario"]["id"], {"Authorization": f"Bearer {resposta.cookies['triagem_session']}"}


def preparar_perfil(fabrica, usuario_id, *, assinatura=True):
    with fabrica() as db:
        perfil = db.get(PerfilDB, usuario_id)
        perfil.dados = {**perfil.dados, "consentimento_ia": True, "onboarding_concluido": True}
        perfil.cv_base = "# Currículo\nExperiência pública com Python e SQL em APIs.\n<!-- PRIVADO -->SEGREDO-CV<!-- /PRIVADO -->"
        db.add(VagaDB(usuario_id=usuario_id, vaga_id=f"vaga-{usuario_id[:8]}", texto="Vaga exclusiva da conta", analise={"empresa": "ACME"}))
        if assinatura:
            db.add(AssinaturaDB(usuario_id=usuario_id, stripe_customer_id=f"cus_{usuario_id}", stripe_subscription_id=f"sub_{usuario_id}", status="active", preco_id="price_test", periodo_atual_fim=datetime.now(timezone.utc) + timedelta(days=30)))
        db.commit()
    return f"vaga-{usuario_id[:8]}"


def test_material_cota_resultado_e_isolamento_por_usuario(ambiente):
    cliente, fabrica, agendadas, _ = ambiente
    ana, headers_ana = cadastrar(cliente)
    bia, headers_bia = cadastrar(cliente, "bia")
    vaga_id = preparar_perfil(fabrica, ana)
    preparar_perfil(fabrica, bia, assinatura=False)
    assert cliente.post(f"/api/vagas/{vaga_id}/material", headers=headers_bia).status_code == 404
    criado = cliente.post(f"/api/vagas/{vaga_id}/material", headers=headers_ana)
    assert criado.status_code == 202, criado.text
    dados = criado.json()
    assert dados["tipo"] == "material" and dados["vaga_alvo_id"] == vaga_id
    assert agendadas == [dados["id"]]
    assert cliente.post(f"/api/vagas/{vaga_id}/material", headers=headers_ana).status_code == 409
    assert cliente.get("/api/buscas/atual", headers=headers_ana).json() is None
    assert cliente.get(f"/api/buscas/{dados['id']}", headers=headers_bia).status_code == 404
    assert cliente.get(f"/api/vagas/{vaga_id}/material", headers=headers_bia).status_code == 404
    with fabrica() as db:
        uso = db.scalar(select(UsoMensalDB).where(UsoMensalDB.usuario_id == ana))
        assert uso.buscas == 0 and uso.analises == 2
        busca = db.get(BuscaDB, dados["id"])
        busca.estado = "concluida"
        busca.resultado = "Material exclusivo da Ana"
        busca.concluida_em = datetime.now(timezone.utc)
        db.commit()
    resultado = cliente.get(f"/api/vagas/{vaga_id}/material", headers=headers_ana)
    assert resultado.status_code == 200
    assert resultado.json()["resultado"] == "Material exclusivo da Ana"


def test_material_exige_consentimento_assinatura_e_cv_com_evidencias(ambiente, monkeypatch):
    cliente, fabrica, agendadas, _ = ambiente
    ana, headers = cadastrar(cliente)
    vaga_id = preparar_perfil(fabrica, ana, assinatura=False)
    assert cliente.post(f"/api/vagas/{vaga_id}/material", headers=headers).status_code == 402
    with fabrica() as db:
        perfil = db.get(PerfilDB, ana)
        perfil.dados = {**perfil.dados, "consentimento_ia": False}
        db.commit()
    assert cliente.post(f"/api/vagas/{vaga_id}/material", headers=headers).status_code == 409
    with fabrica() as db:
        perfil = db.get(PerfilDB, ana)
        perfil.dados = {**perfil.dados, "consentimento_ia": True}
        perfil.cv_base = "# Apenas um título"
        db.commit()
    assert cliente.post(f"/api/vagas/{vaga_id}/material", headers=headers).status_code == 422
    monkeypatch.delenv("GEMINI_API_KEY")
    assert cliente.post(f"/api/vagas/{vaga_id}/material", headers=headers).status_code == 503
    assert not agendadas


def test_exportacao_completa_sem_tokens_e_exclusao_sem_orfaos(ambiente):
    cliente, fabrica, _, canceladas = ambiente
    ana, headers_ana = cadastrar(cliente)
    bia, headers_bia = cadastrar(cliente, "bia")
    preparar_perfil(fabrica, ana)
    preparar_perfil(fabrica, bia)
    with fabrica() as db:
        db.add(BuscaDB(usuario_id=ana, pedido="Busca Ana", estado="concluida", tipo="material", resultado="Material Ana", worker_token="SEGREDO-WORKER"))
        db.add(UsoMensalDB(usuario_id=ana, mes="2026-09", buscas=3, analises=9))
        db.add(RecuperacaoSenhaDB(usuario_id=ana, token_hash="a" * 64, expira_em=datetime.now(timezone.utc) + timedelta(minutes=30)))
        db.commit()
    resposta = cliente.get("/api/auth/exportar", headers=headers_ana)
    assert resposta.status_code == 200
    dados = resposta.json()
    assert dados["usuario"]["id"] == ana
    assert dados["cv_base"].endswith("<!-- /PRIVADO -->")  # exportação pertence ao titular e preserva seus dados
    assert dados["buscas"][0]["resultado"] == "Material Ana"
    assert dados["uso_mensal"][0]["analises"] == 9
    assert dados["aceites_termos"][0]["aceito_em"]
    assert "SEGREDO-WORKER" not in resposta.text
    assert "senha_hash" not in resposta.text and "token_hash" not in resposta.text
    assert bia not in resposta.text
    assert cliente.request("DELETE", "/api/auth/me", headers=headers_ana, json={"senha": "incorreta"}).status_code == 403
    assert not canceladas
    excluida = cliente.request("DELETE", "/api/auth/me", headers=headers_ana, json={"senha": "senha-forte-123"})
    assert excluida.status_code == 204, excluida.text
    assert canceladas == [ana]
    assert cliente.get("/api/auth/me", headers=headers_ana).status_code == 401
    assert cliente.get("/api/auth/me", headers=headers_bia).status_code == 200
    with fabrica() as db:
        assert db.get(Usuario, ana) is None
        assert db.get(Usuario, bia) is not None
        for tabela in Base.metadata.sorted_tables:
            if "usuario_id" in tabela.c:
                assert not db.execute(select(tabela).where(tabela.c.usuario_id == ana)).first(), tabela.name


def test_exclusao_preserva_conta_quando_cancelamento_cobranca_falha(ambiente, monkeypatch):
    cliente, fabrica, _, _ = ambiente
    ana, headers = cadastrar(cliente)
    preparar_perfil(fabrica, ana)

    def falhar(*_):
        raise HTTPException(503, "Cobrança temporariamente indisponível.")

    monkeypatch.setattr(api_app, "encerrar_cobranca_para_exclusao", falhar)
    resposta = cliente.request("DELETE", "/api/auth/me", headers=headers, json={"senha": "senha-forte-123"})
    assert resposta.status_code == 503
    with fabrica() as db:
        assert db.get(Usuario, ana) and db.get(PerfilDB, ana) and db.get(AssinaturaDB, ana)
    assert cliente.get("/api/auth/me", headers=headers).status_code == 200


def test_revogar_consentimento_cancela_fila_somente_da_conta(ambiente):
    cliente, fabrica, _, _ = ambiente
    ana, headers = cadastrar(cliente)
    bia, _ = cadastrar(cliente, "bia")
    preparar_perfil(fabrica, ana)
    preparar_perfil(fabrica, bia)
    with fabrica() as db:
        a = BuscaDB(usuario_id=ana, pedido="Ana", estado="processando", worker_token="token-ana", expira_em=datetime.now(timezone.utc) + timedelta(minutes=1))
        b = BuscaDB(usuario_id=bia, pedido="Bia", estado="pendente")
        db.add_all([a, b])
        db.commit()
        a_id, b_id = a.id, b.id
    perfil = cliente.get("/api/perfil", headers=headers).json()
    perfil["consentimento_ia"] = False
    assert cliente.put("/api/perfil", headers=headers, json=perfil).status_code == 200
    with fabrica() as db:
        cancelada = db.get(BuscaDB, a_id)
        assert cancelada.estado == "falhou" and cancelada.worker_token is None
        assert db.get(BuscaDB, b_id).estado == "pendente"
    assert cliente.post("/api/buscas", headers=headers, json={"limite": 1}).status_code == 409


def test_ready_detecta_banco_indisponivel_sem_detalhes_internos(ambiente):
    cliente, _, _, _ = ambiente
    assert cliente.get("/ready").json() == {"status": "ready"}

    class BancoQuebrado:
        def execute(self, *_):
            raise OSError("postgres://SEGREDO-SENHA")

    api_app.app.dependency_overrides[sessao] = lambda: BancoQuebrado()
    resposta = cliente.get("/ready")
    assert resposta.status_code == 503
    assert "SEGREDO-SENHA" not in resposta.text


def producao_configurada(monkeypatch):
    # Limpa opções do ambiente do operador sem ler/exibir credenciais.
    for nome in tuple(os.environ):
        if nome.startswith(("TRIAGEM_", "STRIPE_")) or nome in {"DATABASE_URL", "GEMINI_API_KEY"}:
            monkeypatch.delenv(nome)
    valores = {
        "TRIAGEM_ENV": "production", "DATABASE_URL": "postgresql+psycopg://triagem:senha@db/triagem",
        "TRIAGEM_JWT_SECRET": "abcDEF1234567890!@#$%^&*()-_=+ghiJKLmnopQRSTuvWXYZ789",
        "TRIAGEM_PUBLIC_URL": "https://carreira.test", "GEMINI_API_KEY": "chave-validar-no-operador",
        "STRIPE_SECRET_KEY": "sk_live_teste_sintetico", "STRIPE_WEBHOOK_SECRET": "whsec_sintetico", "STRIPE_PRICE_ID": "price_sintetico",
        "TRIAGEM_SUPPORT_EMAIL": "suporte@carreira.test", "TRIAGEM_TERMS_URL": "https://carreira.test/termos",
        "TRIAGEM_PRIVACY_URL": "https://carreira.test/privacidade", "TRIAGEM_SMTP_HOST": "smtp.carreira.test",
        "TRIAGEM_SMTP_FROM": "conta@carreira.test", "TRIAGEM_SMTP_SECURITY": "starttls",
        "TRIAGEM_CORS_ORIGINS": "https://carreira.test", "TRIAGEM_WORKER_MODE": "external",
    }
    for nome, valor in valores.items():
        monkeypatch.setenv(nome, valor)


def test_config_producao_completa_e_publica_sem_segredos(monkeypatch):
    producao_configurada(monkeypatch)
    assert config.problemas_configuracao() == []
    config.validar_configuracao()
    assert set(config.configuracao_publica()) == {"nome_servico", "suporte_email", "termos_url", "privacidade_url"}
    monkeypatch.setenv("TRIAGEM_JWT_SECRET", "fraco")
    with pytest.raises(RuntimeError, match="TRIAGEM_JWT_SECRET"):
        config.validar_configuracao()


@pytest.mark.parametrize("nome,valor", [
    ("TRIAGEM_ENV", "prod-erro"), ("TRIAGEM_JOB_TIMEOUT_SECONDS", "0"),
    ("TRIAGEM_TOKEN_MINUTES", "nan"), ("TRIAGEM_MONTHLY_SEARCH_LIMIT", "0"),
    ("TRIAGEM_WORKER_MODE", "disabled"), ("TRIAGEM_SMTP_SECURITY", "plain"),
    ("DATABASE_URL", "sqlite:///producao.db"), ("STRIPE_SECRET_KEY", "sk_test_123"),
    ("TRIAGEM_CORS_ORIGINS", "*"), ("TRIAGEM_PUBLIC_URL", "http://localhost"),
    ("TRIAGEM_SUPPORT_EMAIL", ""), ("TRIAGEM_SMTP_PORT", "abc"),
    ("TRIAGEM_PUBLIC_URL", "https://carreira.test/?token=indevido"),
])
def test_config_producao_rejeita_parametros_que_quebram_servico(monkeypatch, nome, valor):
    producao_configurada(monkeypatch)
    monkeypatch.setenv(nome, valor)
    assert config.problemas_configuracao(), nome
