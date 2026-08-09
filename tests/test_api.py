"""Autenticação, persistência e isolamento multiusuário da API SaaS."""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api import app as api_app
from api.database import Base, VagaDB, sessao


def _entrada(empresa="ACME", status="novo", score=80.0):
    return VagaDB(
        vaga_id=f"{empresa.lower()}123456", status=status, score_final=score,
        analisado_em="2026-08-07T10:00:00", texto="vaga",
        analise={
            "titulo_normalizado": "Backend Engineer", "empresa": empresa,
            "regime": "remoto", "localizacao": "Brasil", "nivel_real": "jr",
            "idioma_trabalho": "pt", "stack_exigida": ["Python"],
            "stack_desejavel": [], "alertas": [], "descartada": False,
        },
    )


def _cadastro(cliente, email):
    resposta = cliente.post("/api/auth/cadastro", json={"email": email, "senha": "senha-forte-123"})
    assert resposta.status_code == 201
    dados = resposta.json()
    dados["_token"] = resposta.cookies.get("triagem_session")
    assert dados["_token"]
    assert "access_token" not in dados
    return dados


def _headers(sessao_auth):
    return {"Authorization": f"Bearer {sessao_auth['_token']}"}


def test_health_e_rotas_privadas_exigem_login():
    cliente = TestClient(api_app.app)
    assert cliente.get("/health").json() == {"status": "ok"}
    assert cliente.get("/api/vagas").status_code == 401


def test_fluxo_completo_e_isolamento_entre_usuarios(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

    def sessao_teste():
        with TestSession() as db:
            yield db

    api_app.app.dependency_overrides[sessao] = sessao_teste
    cliente = TestClient(api_app.app)
    agendadas = []
    monkeypatch.setenv("GEMINI_API_KEY", "chave-teste")
    monkeypatch.setattr(api_app, "agendar", agendadas.append)
    ana = _cadastro(cliente, "ana@example.com")
    bia = _cadastro(cliente, "bia@example.com")

    assert cliente.post(
        "/api/auth/cadastro", json={"email": "ana@example.com", "senha": "outra-senha-123"}
    ).status_code == 409
    assert cliente.post(
        "/api/auth/login", json={"email": "ana@example.com", "senha": "errada"}
    ).status_code == 401
    login = cliente.post(
        "/api/auth/login", json={"email": "ana@example.com", "senha": "senha-forte-123"}
    )
    assert login.status_code == 200
    assert cliente.get("/api/auth/me", headers=_headers(ana)).json()["email"] == "ana@example.com"

    perfil_ana = cliente.get("/api/perfil", headers=_headers(ana)).json()
    perfil_ana.update({
        "nome": "Ana", "areas": ["QA"], "cidades_aceitas": ["Recife"],
        "senioridades": ["Júnior"], "consentimento_ia": True,
        "onboarding_concluido": True,
    })
    assert cliente.put("/api/perfil", json=perfil_ana, headers=_headers(ana)).status_code == 200
    assert cliente.get("/api/perfil", headers=_headers(bia)).json()["nome"] == "bia"

    assert cliente.put(
        "/api/cv", json={"conteudo": "# CV exclusivo da Ana"}, headers=_headers(ana)
    ).status_code == 200
    assert cliente.get("/api/cv", headers=_headers(bia)).json()["conteudo"] == ""
    assert cliente.put("/api/cv", json={"conteudo": " "}, headers=_headers(ana)).status_code == 400

    iniciada = cliente.post(
        "/api/buscas", json={"pedido": "Python remoto", "limite": 5}, headers=_headers(ana)
    )
    assert iniciada.status_code == 202
    busca_id = iniciada.json()["id"]
    assert agendadas == [busca_id]
    assert iniciada.json()["estado"] == "pendente"
    assert cliente.post("/api/buscas", json={}, headers=_headers(ana)).status_code == 409
    assert cliente.get(f"/api/buscas/{busca_id}", headers=_headers(bia)).status_code == 404
    assert cliente.get("/api/buscas/atual", headers=_headers(ana)).json()["id"] == busca_id

    with TestSession() as db:
        vaga_ana = _entrada("ACME")
        vaga_ana.usuario_id = ana["usuario"]["id"]
        vaga_bia = _entrada("BETA")
        vaga_bia.usuario_id = bia["usuario"]["id"]
        db.add_all([vaga_ana, vaga_bia])
        db.commit()

    vagas_ana = cliente.get("/api/vagas", headers=_headers(ana)).json()
    vagas_bia = cliente.get("/api/vagas", headers=_headers(bia)).json()
    assert [vaga["empresa"] for vaga in vagas_ana] == ["ACME"]
    assert [vaga["empresa"] for vaga in vagas_bia] == ["BETA"]
    assert cliente.get("/api/vagas/beta", headers=_headers(ana)).status_code == 404

    resposta = cliente.patch(
        "/api/vagas/acme/status", json={"status": "aplicado"}, headers=_headers(ana)
    )
    assert resposta.status_code == 200
    assert resposta.json()["status"] == "aplicado"
    assert cliente.get("/api/stats", headers=_headers(ana)).json()["por_status"]["aplicado"] == 1
    assert cliente.get("/api/stats", headers=_headers(bia)).json()["por_status"]["aplicado"] == 0

    exportado = cliente.get("/api/auth/exportar", headers=_headers(ana))
    assert exportado.status_code == 200
    assert exportado.json()["usuario"]["email"] == "ana@example.com"
    assert exportado.json()["vagas"][0]["analise"]["empresa"] == "ACME"
    assert cliente.request(
        "DELETE", "/api/auth/me", json={"senha": "incorreta"}, headers=_headers(bia)
    ).status_code == 403
    assert cliente.request(
        "DELETE", "/api/auth/me", json={"senha": "senha-forte-123"}, headers=_headers(bia)
    ).status_code == 204
    assert cliente.get("/api/auth/me", headers=_headers(bia)).status_code == 401
    api_app.app.dependency_overrides.clear()
