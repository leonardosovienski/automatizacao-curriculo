"""Testes da API REST (api/app.py) sobre o histórico."""

import json

import filelock
import pytest
from fastapi.testclient import TestClient

from api import app as api_app
from triagem import historico


def _entrada(
    *,
    empresa="ACME",
    titulo="Backend Engineer",
    status="novo",
    score=80.0,
    regime="remoto",
    descartada=False,
    motivo_descarte=None,
    notas=None,
    alertas=None,
):
    return {
        "analisado_em": "2026-08-07T10:00:00",
        "status": status,
        "score_final": None if descartada else score,
        "pipeline_version": 2,
        "texto": f"Vaga: {titulo}\nEmpresa: {empresa}",
        "analise": {
            "titulo_normalizado": titulo,
            "empresa": empresa,
            "regime": regime,
            "localizacao": "Brasil",
            "nivel_real": "pleno_disfarcado",
            "stack_exigida": ["Python"],
            "stack_desejavel": ["FastAPI"],
            "idioma_trabalho": "pt",
            "link": "https://example.com/vaga",
            "origem": "teste",
            "publicada_em": "",
            "descartada": descartada,
            "motivo_descarte": motivo_descarte,
            "notas": notas,
            "alertas": alertas or [],
        },
    }


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    caminho = tmp_path / "historico.json"
    monkeypatch.setattr(historico, "ARQUIVO", caminho)
    monkeypatch.setattr(api_app, "LOCK", filelock.FileLock(str(caminho) + ".lock"))
    return TestClient(api_app.app)


def _escrever_historico(caminho, dados):
    caminho.write_text(json.dumps(dados), encoding="utf-8")


def test_health(cliente):
    resp = cliente.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_stats_sem_historico(cliente):
    resp = cliente.get("/api/stats")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
    assert resp.json()["por_status"]["novo"] == 0


def test_stats_conta_por_status(cliente, tmp_path):
    _escrever_historico(
        historico.ARQUIVO,
        {
            "a1": _entrada(status="novo"),
            "a2": _entrada(status="aplicado"),
            "a3": _entrada(status="aplicado"),
        },
    )
    resp = cliente.get("/api/stats")
    body = resp.json()
    assert body["total"] == 3
    assert body["por_status"]["novo"] == 1
    assert body["por_status"]["aplicado"] == 2


def test_listar_vagas_vazio(cliente):
    resp = cliente.get("/api/vagas")
    assert resp.status_code == 200
    assert resp.json() == []


def test_listar_vagas_ordena_por_score_desc(cliente):
    _escrever_historico(
        historico.ARQUIVO,
        {
            "baixo": _entrada(empresa="Baixo", score=40.0),
            "alto": _entrada(empresa="Alto", score=95.0),
            "descartado": _entrada(
                empresa="Descartado", descartada=True, motivo_descarte="fora do raio"
            ),
        },
    )
    resp = cliente.get("/api/vagas")
    assert resp.status_code == 200
    empresas = [v["empresa"] for v in resp.json()]
    # descartada (score_final None) sempre por último
    assert empresas == ["Alto", "Baixo", "Descartado"]


def test_listar_vagas_filtra_por_status(cliente):
    _escrever_historico(
        historico.ARQUIVO,
        {
            "a1": _entrada(status="novo"),
            "a2": _entrada(status="aplicado"),
        },
    )
    resp = cliente.get("/api/vagas", params={"status": "aplicado"})
    assert resp.status_code == 200
    vagas = resp.json()
    assert len(vagas) == 1
    assert vagas[0]["status"] == "aplicado"


def test_listar_vagas_status_invalido_400(cliente):
    resp = cliente.get("/api/vagas", params={"status": "nao_existe"})
    assert resp.status_code == 400


def test_vaga_expoe_notas_e_stack(cliente):
    notas = {
        "d1_crescimento": {"nota": 8, "justificativa": "boa"},
    }
    _escrever_historico(
        historico.ARQUIVO,
        {"a1": _entrada(notas=notas, alertas=["cuidado"])},
    )
    resp = cliente.get("/api/vagas")
    vaga = resp.json()[0]
    assert vaga["notas"]["d1_crescimento"]["nota"] == 8
    assert vaga["stack_exigida"] == ["Python"]
    assert vaga["alertas"] == ["cuidado"]


def test_obter_vaga_por_id(cliente):
    _escrever_historico(historico.ARQUIVO, {"a1": _entrada(empresa="Única")})
    resp = cliente.get("/api/vagas/a1")
    assert resp.status_code == 200
    assert resp.json()["empresa"] == "Única"


def test_obter_vaga_inexistente_404(cliente):
    resp = cliente.get("/api/vagas/nao-existe")
    assert resp.status_code == 404


def test_atualizar_status_persiste_no_arquivo(cliente):
    _escrever_historico(historico.ARQUIVO, {"a1": _entrada(status="novo")})
    resp = cliente.patch("/api/vagas/a1/status", json={"status": "aplicado"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "aplicado"

    persistido = historico.carregar()
    assert persistido["a1"]["status"] == "aplicado"


def test_atualizar_status_aceita_prefixo_de_id(cliente):
    _escrever_historico(historico.ARQUIVO, {"abcdef1234": _entrada(status="novo")})
    resp = cliente.patch("/api/vagas/abcdef/status", json={"status": "entrevista"})
    assert resp.status_code == 200
    assert resp.json()["id"] == "abcdef1234"


def test_atualizar_status_invalido_400(cliente):
    _escrever_historico(historico.ARQUIVO, {"a1": _entrada(status="novo")})
    resp = cliente.patch("/api/vagas/a1/status", json={"status": "nao_existe"})
    assert resp.status_code == 400


def test_atualizar_status_id_inexistente_404(cliente):
    _escrever_historico(historico.ARQUIVO, {"a1": _entrada(status="novo")})
    resp = cliente.patch("/api/vagas/zzz/status", json={"status": "aplicado"})
    assert resp.status_code == 404
