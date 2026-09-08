"""Regressões de segurança executando HTTP real e persistência SQL."""

import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api import auth_routes
from api.auth import COOKIE_SESSAO, _segredo, usuario_atual, verificar_senha
from api.auth_models import AceiteTermosDB, LimiteAuthDB, RecuperacaoSenhaDB, SessaoAuthDB
from api.database import Base, Usuario, sessao
from api.security import SecurityMiddleware, _origem, limitar_tentativas


@pytest.fixture
def ambiente(monkeypatch):
    monkeypatch.delenv("TRIAGEM_ENV", raising=False)
    monkeypatch.delenv("TRIAGEM_PUBLIC_URL", raising=False)
    monkeypatch.delenv("TRIAGEM_SMTP_HOST", raising=False)
    monkeypatch.setenv("TRIAGEM_JWT_SECRET", "segredo-exclusivo-para-testes-seguranca-12345")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    fabrica = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

    def db_teste():
        with fabrica() as db:
            yield db

    app = FastAPI()
    app.include_router(auth_routes.router)
    app.add_middleware(SecurityMiddleware, origens=["http://localhost:5173"])
    app.dependency_overrides[sessao] = db_teste

    @app.get("/privado")
    def privado(usuario=Depends(usuario_atual)):  # noqa: B008
        return {"id": usuario.id}

    @app.post("/privado")
    def alterar(usuario=Depends(usuario_atual)):  # noqa: B008
        return {"id": usuario.id}

    with TestClient(app, headers={"Origin": "http://testserver"}) as cliente:
        yield cliente, fabrica
    engine.dispose()


def cadastrar(cliente, email="ana@example.com", **campos):
    resposta = cliente.post("/api/auth/cadastro", json={"email": email, "senha": "senha-forte-123", **campos})
    assert resposta.status_code == 201, resposta.text
    return resposta.json()["usuario"]["id"], resposta.cookies[COOKIE_SESSAO]


def entrar(cliente, senha="senha-forte-123"):
    return cliente.post("/api/auth/login", json={"email": "ana@example.com", "senha": senha})


def configurar_email(monkeypatch):
    monkeypatch.setenv("TRIAGEM_PUBLIC_URL", "https://app.example.com")
    monkeypatch.setenv("TRIAGEM_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("TRIAGEM_SMTP_FROM", "conta@example.com")
    enviadas = []
    monkeypatch.setattr(auth_routes, "enviar_recuperacao", lambda *dados: enviadas.append(dados))
    return enviadas


def token_email(enviadas):
    return parse_qs(urlsplit(enviadas[-1][1]).fragment)["token"][0]


def test_logout_revoga_token_copiado_sem_encerrar_outro_dispositivo(ambiente):
    cliente, fabrica = ambiente
    usuario_id, token_antigo = cadastrar(cliente)
    nova = entrar(cliente)
    token_novo = nova.cookies[COOKIE_SESSAO]
    assert token_novo != token_antigo
    assert cliente.post("/api/auth/logout", headers={"Authorization": f"Bearer {token_antigo}"}).status_code == 204
    assert cliente.get("/privado", headers={"Authorization": f"Bearer {token_antigo}"}).status_code == 401
    assert cliente.get("/privado", headers={"Authorization": f"Bearer {token_novo}"}).status_code == 200
    with fabrica() as db:
        assert len(db.scalars(select(SessaoAuthDB).where(SessaoAuthDB.usuario_id == usuario_id)).all()) == 1
    assert cliente.post("/api/auth/logout", headers={"Authorization": "Bearer invalido"}).status_code == 204
    assert cliente.post("/api/auth/logout").status_code == 204


def test_jwt_legado_adulterado_e_sessao_expirada_sao_rejeitados(ambiente):
    cliente, fabrica = ambiente
    usuario_id, token = cadastrar(cliente)
    payload = jwt.decode(token, _segredo(), algorithms=["HS256"], audience="triagem-api")
    for campo in ("jti", "exp", "iss", "aud"):
        incompleto = {chave: valor for chave, valor in payload.items() if chave != campo}
        adulterado = jwt.encode(incompleto, _segredo(), algorithm="HS256")
        assert cliente.get("/privado", headers={"Authorization": f"Bearer {adulterado}"}).status_code == 401
    estranho = jwt.encode({**payload, "jti": 12}, _segredo(), algorithm="HS256")
    assert cliente.get("/privado", headers={"Authorization": f"Bearer {estranho}"}).status_code == 401
    with fabrica() as db:
        registro = db.get(SessaoAuthDB, payload["jti"])
        registro.expira_em = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    assert cliente.get("/privado").status_code == 401
    cliente.cookies.clear()
    assert cliente.get("/privado").status_code == 401
    assert usuario_id


def test_login_inativo_hash_invalido_e_limite_senha(ambiente):
    cliente, fabrica = ambiente
    usuario_id, _ = cadastrar(cliente)
    with fabrica() as db:
        usuario = db.get(Usuario, usuario_id)
        usuario.ativo = False
        db.commit()
    assert entrar(cliente).status_code == 401
    assert cliente.get("/privado").status_code == 401
    assert entrar(cliente, "x" * 129).status_code == 422
    assert not verificar_senha("senha", "hash-corrompido")
    assert cliente.post("/api/auth/login", json={"email": "ausente@example.com", "senha": "senha-forte-123"}).status_code == 401


def test_cookie_seguro_e_aceite_obrigatorio_producao(ambiente, monkeypatch):
    cliente, fabrica = ambiente
    monkeypatch.setenv("TRIAGEM_ENV", "production")
    monkeypatch.setenv("TRIAGEM_TERMS_VERSION", "versao-2")
    monkeypatch.setenv("TRIAGEM_TERMS_URL", "https://example.com/termos")
    resposta = cliente.post("/api/auth/cadastro", json={"email": "ana@example.com", "senha": "senha-forte-123"})
    assert resposta.status_code == 422
    assert resposta.headers["strict-transport-security"] == "max-age=31536000"
    usuario_id, _ = cadastrar(cliente, aceite_termos=True)
    with fabrica() as db:
        aceite = db.get(AceiteTermosDB, usuario_id)
        assert aceite.versao == "versao-2"
        assert aceite.termos_url == "https://example.com/termos"
        assert aceite.aceito_em
    cookie = next(cookie for cookie in cliente.cookies.jar if cookie.name == COOKIE_SESSAO)
    assert cookie.secure
    assert cookie.has_nonstandard_attr("HttpOnly")
    assert cookie.get_nonstandard_attr("SameSite") == "lax"


def test_csrf_login_cadastro_cookie_e_bearer(ambiente):
    cliente, _ = ambiente
    malicioso = cliente.post("/api/auth/cadastro", headers={"Origin": "https://evil.example"}, json={"email": "ana@example.com", "senha": "senha-forte-123"})
    assert malicioso.status_code == 403
    _, token = cadastrar(cliente)
    assert entrar(cliente).status_code == 200
    assert cliente.post("/api/auth/logout", headers={"Origin": "null"}).status_code == 403
    assert cliente.post("/privado", headers={"Origin": "https://evil.example"}).status_code == 403
    cliente.headers.pop("origin")
    assert cliente.post("/privado").status_code == 403
    assert cliente.post("/privado", headers={"Referer": "http://localhost:5173/perfil"}).status_code == 200
    assert cliente.post("/privado", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    cliente.cookies.clear()
    assert cliente.post("/api/auth/logout", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert cliente.post("/api/auth/logout").status_code == 204


def test_corpo_limitado_inclusive_sem_content_length_e_headers(ambiente):
    cliente, _ = ambiente
    resposta = cliente.post("/api/auth/login", content=b"x" * 1_048_577)
    assert resposta.status_code == 413
    assert resposta.headers["cache-control"] == "no-store"
    assert resposta.headers["x-content-type-options"] == "nosniff"
    assert resposta.headers["referrer-policy"] == "no-referrer"
    assert resposta.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert cliente.post("/api/auth/login", content=iter([b"x" * 600_000, b"x" * 600_000])).status_code == 413
    assert cliente.post("/api/auth/login", headers={"Content-Length": "invalido"}).status_code == 400
    assert cliente.post("/api/auth/login", headers={"Content-Length": "-1"}).status_code == 400


@pytest.mark.parametrize("url,esperado", [
    ("https://EXAMPLE.com:443/rota", "https://example.com"),
    ("http://example.com:8080", "http://example.com:8080"),
    ("http://[::1]:5173/test", "http://[::1]:5173"),
    ("https://usuario@example.com", ""),
    ("https://example.com:abc", ""), ("null", ""),
])
def test_normalizacao_origem(url, esperado):
    assert _origem(url) == esperado


def test_limite_persistido_nao_pode_ser_burlado_por_forwarded_for(ambiente):
    cliente, fabrica = ambiente
    for tentativa in range(8):
        resposta = cliente.post("/api/auth/login", headers={"X-Forwarded-For": f"192.0.2.{tentativa}"}, json={"email": "ausente@example.com", "senha": "incorreta"})
        assert resposta.status_code == 401
    bloqueio = cliente.post("/api/auth/login", headers={"X-Forwarded-For": "203.0.113.1"}, json={"email": "ausente@example.com", "senha": "incorreta"})
    assert bloqueio.status_code == 429
    assert int(bloqueio.headers["retry-after"]) > 0
    with fabrica() as db:
        registros = db.scalars(select(LimiteAuthDB)).all()
        assert len(registros) == 2
        assert all(len(item.chave) == 64 and "example" not in item.chave for item in registros)
        for item in registros:
            item.expira_em = 0
        db.commit()
    assert cliente.post("/api/auth/login", json={"email": "ausente@example.com", "senha": "incorreta"}).status_code == 401


def test_limite_atomico_entre_sessoes_concorrentes(tmp_path, monkeypatch):
    monkeypatch.setenv("TRIAGEM_JWT_SECRET", "segredo-exclusivo-para-concorrencia-1234")
    engine = create_engine(f"sqlite:///{(tmp_path / 'limites.db').as_posix()}")
    Base.metadata.create_all(engine)
    fabrica = sessionmaker(bind=engine)

    def tentar(_):
        with fabrica() as db:
            try:
                limitar_tentativas(db, "mesma-chave", limite=4)
                return 200
            except HTTPException as erro:
                return erro.status_code

    with ThreadPoolExecutor(max_workers=8) as executor:
        resultados = list(executor.map(tentar, range(16)))
    assert resultados.count(200) == 4
    assert resultados.count(429) == 12
    with fabrica() as db:
        assert db.scalar(select(LimiteAuthDB)).tentativas == 5
    engine.dispose()


def test_recuperacao_generica_token_hash_uso_unico_e_revogacao(ambiente, monkeypatch):
    cliente, fabrica = ambiente
    _, token_antigo = cadastrar(cliente)
    enviadas = configurar_email(monkeypatch)
    resposta = cliente.post("/api/auth/recuperar-senha", json={"email": "ana@example.com"})
    desconhecido = cliente.post("/api/auth/recuperar-senha", json={"email": "desconhecido@example.com"})
    assert resposta.status_code == desconhecido.status_code == 202
    assert resposta.json() == desconhecido.json()
    assert len(enviadas) == 1
    token = token_email(enviadas)
    assert token not in resposta.text
    with fabrica() as db:
        registro = db.scalar(select(RecuperacaoSenhaDB))
        assert registro.token_hash == hashlib.sha256(token.encode()).hexdigest()
    nova_senha = "senha-nova-forte-456"
    redefinida = cliente.post("/api/auth/redefinir-senha", json={"token": token, "senha": nova_senha})
    assert redefinida.status_code == 200
    assert COOKIE_SESSAO not in redefinida.cookies
    assert cliente.get("/privado", headers={"Authorization": f"Bearer {token_antigo}"}).status_code == 401
    assert cliente.post("/api/auth/redefinir-senha", json={"token": token, "senha": "outra-senha-789"}).status_code == 400
    assert entrar(cliente).status_code == 401
    assert entrar(cliente, nova_senha).status_code == 200


def test_recuperacao_substitui_token_anterior_e_expira(ambiente, monkeypatch):
    cliente, fabrica = ambiente
    cadastrar(cliente)
    enviadas = configurar_email(monkeypatch)
    cliente.post("/api/auth/recuperar-senha", json={"email": "ana@example.com"})
    anterior = token_email(enviadas)
    cliente.post("/api/auth/recuperar-senha", json={"email": "ana@example.com"})
    atual = token_email(enviadas)
    assert anterior != atual
    assert cliente.post("/api/auth/redefinir-senha", json={"token": anterior, "senha": "nova-senha-123"}).status_code == 400
    with fabrica() as db:
        registro = db.scalar(select(RecuperacaoSenhaDB))
        registro.expira_em = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    assert cliente.post("/api/auth/redefinir-senha", json={"token": atual, "senha": "nova-senha-123"}).status_code == 400


def test_recuperacao_sem_smtp_e_config_insegura_falham_sem_enumerar(ambiente, monkeypatch):
    cliente, _ = ambiente
    cadastrar(cliente)
    for email in ("ana@example.com", "desconhecido@example.com"):
        assert cliente.post("/api/auth/recuperar-senha", json={"email": email}).status_code == 503
    configurar_email(monkeypatch)
    monkeypatch.setenv("TRIAGEM_ENV", "production")
    monkeypatch.setenv("TRIAGEM_SMTP_SECURITY", "plain")
    assert cliente.post("/api/auth/recuperar-senha", json={"email": "ana@example.com"}).status_code == 503


def test_envio_smtp_tls_timeout_e_log_sem_segredo(monkeypatch, caplog):
    monkeypatch.setenv("TRIAGEM_SMTP_FROM", "conta@example.com")
    monkeypatch.setenv("TRIAGEM_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("TRIAGEM_SMTP_USERNAME", "conta")
    monkeypatch.setenv("TRIAGEM_SMTP_PASSWORD", "senha-smtp")
    chamadas = []

    class SMTPFalso:
        def __init__(self, host, porta, **kwargs):
            chamadas.append((host, porta, kwargs["timeout"]))

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def starttls(self, **_):
            chamadas.append("tls")

        def login(self, usuario, senha):
            chamadas.append((usuario, senha))

        def send_message(self, mensagem):
            chamadas.append(mensagem)

    monkeypatch.setattr(auth_routes.smtplib, "SMTP", SMTPFalso)
    auth_routes.enviar_recuperacao("ana@example.com", "https://example.com/#token=SEGREDO", "starttls")
    assert chamadas[:3] == [("smtp.example.com", 587, 10), "tls", ("conta", "senha-smtp")]
    assert "SEGREDO" in chamadas[3].get_content()

    def falhar(*_, **__):
        raise OSError("SEGREDO")

    monkeypatch.setattr(auth_routes.smtplib, "SMTP", falhar)
    with caplog.at_level(logging.ERROR):
        auth_routes.enviar_recuperacao("ana@example.com", "https://example.com/#token=SEGREDO", "starttls")
    assert "Falha no envio" in caplog.text
    assert "SEGREDO" not in caplog.text
    assert "ana@example.com" not in caplog.text
