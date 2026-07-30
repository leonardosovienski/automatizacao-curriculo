"""Testes de prontidão: empacotamento, CLI e estados auxiliares."""

import json
import os
import sys
from pathlib import Path

import pytest

from triagem import alvos_ats, cache, curriculo, historico, replay
from triagem.analisador import cv_prompt, system_prompt
from triagem.ats import ResultadoSyncATS
from triagem.cli import main


@pytest.fixture
def estado_isolado(tmp_path, monkeypatch):
    caminhos = {
        "TRIAGEM_HISTORICO": tmp_path / "historico.json",
        "TRIAGEM_CACHE": tmp_path / "cache.json",
        "TRIAGEM_ALVOS_ATS": tmp_path / "alvos.json",
        "TRIAGEM_REPLAY": tmp_path / "replay",
        "TRIAGEM_CV_BASE": tmp_path / "cv.md",
    }
    for nome, caminho in caminhos.items():
        monkeypatch.setenv(nome, str(caminho))
    monkeypatch.setattr(historico, "ARQUIVO", caminhos["TRIAGEM_HISTORICO"])
    monkeypatch.setattr(cache, "ARQUIVO", caminhos["TRIAGEM_CACHE"])
    monkeypatch.setattr(alvos_ats, "ARQUIVO", caminhos["TRIAGEM_ALVOS_ATS"])
    monkeypatch.setattr(replay, "DIRETORIO", caminhos["TRIAGEM_REPLAY"])
    monkeypatch.setattr(curriculo, "CV_BASE", caminhos["TRIAGEM_CV_BASE"])
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return caminhos


def _executar(monkeypatch, argumentos):
    monkeypatch.setattr(sys, "argv", ["triar", *argumentos])
    return main()


def test_prompts_de_runtime_ficam_dentro_do_pacote():
    raiz = Path(__file__).resolve().parents[1]
    configuracao = (raiz / "pyproject.toml").read_text(encoding="utf-8")
    assert 'triagem = ["prompts/*.md"]' in configuracao
    assert len(system_prompt()) > 1_000
    assert len(cv_prompt()) > 500
    assert not list((raiz / "prompts").glob("*.md"))


def test_cli_sem_argumentos_exibe_ajuda(monkeypatch, capsys):
    assert _executar(monkeypatch, []) == 1
    assert "Triagem de vagas personalizada" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("argumentos", "codigo", "trecho"),
    [
        (["historico"], 0, "Histórico vazio"),
        (["limpar-cache", "--tudo"], 0, "Cache limpo"),
        (["sync-ats"], 0, "Nenhum ATS ativo"),
        (["analisar"], 1, "Informe um arquivo"),
        (["buscar"], 1, "GEMINI_API_KEY"),
        (["status", "inexistente", "aplicado"], 1, "Nenhuma vaga"),
        (["cv", "inexistente"], 1, "Nenhuma vaga"),
    ],
)
def test_comandos_cli_basicos_sao_previsiveis(
    estado_isolado, monkeypatch, capsys, argumentos, codigo, trecho
):
    assert _executar(monkeypatch, argumentos) == codigo
    assert trecho in capsys.readouterr().out


def test_cli_recarrega_todos_os_caminhos_depois_do_dotenv(
    estado_isolado, monkeypatch, capsys
):
    assert _executar(monkeypatch, ["historico"]) == 0
    assert historico.ARQUIVO == estado_isolado["TRIAGEM_HISTORICO"]
    assert cache.ARQUIVO == estado_isolado["TRIAGEM_CACHE"]
    assert alvos_ats.ARQUIVO == estado_isolado["TRIAGEM_ALVOS_ATS"]
    assert replay.DIRETORIO == estado_isolado["TRIAGEM_REPLAY"]
    assert curriculo.CV_BASE == estado_isolado["TRIAGEM_CV_BASE"]
    capsys.readouterr()


def test_cli_retrocompativel_chega_ao_guardrail_da_api(
    estado_isolado, monkeypatch, capsys, tmp_path
):
    entrada = tmp_path / "vaga.json"
    entrada.write_text(
        json.dumps(
            {
                "titulo": "DevOps Júnior",
                "empresa": "ACME",
                "descricao": "Docker, CI/CD e Azure.",
                "link": "https://example.test/jobs/1",
            }
        ),
        encoding="utf-8",
    )
    assert _executar(monkeypatch, [str(entrada)]) == 1
    assert "GEMINI_API_KEY" in capsys.readouterr().out


def test_alvos_ats_persistem_e_aplicam_resultados(tmp_path, monkeypatch):
    arquivo = tmp_path / "alvos.json"
    monkeypatch.setattr(alvos_ats, "ARQUIVO", arquivo)

    alvos_ats.registrar("greenhouse", "acme")
    estado = alvos_ats.carregar()
    assert alvos_ats.ativos(estado)[0]["token"] == "acme"

    alvos_ats.aplicar_resultado(
        estado, ResultadoSyncATS("greenhouse", "acme", "falha", erro="Timeout")
    )
    assert estado["alvos"]["greenhouse:acme"]["ultima_falha"] == "Timeout"

    alvos_ats.aplicar_resultado(
        estado, ResultadoSyncATS("greenhouse", "acme", "inativo")
    )
    assert alvos_ats.ativos(estado) == []
    alvos_ats.salvar(estado)
    assert alvos_ats.carregar()["alvos"]["greenhouse:acme"]["status"] == "inativo"


def test_alvos_ats_rejeitam_shape_invalido(tmp_path, monkeypatch):
    arquivo = tmp_path / "alvos.json"
    arquivo.write_text('{"alvos": []}', encoding="utf-8")
    monkeypatch.setattr(alvos_ats, "ARQUIVO", arquivo)
    with pytest.raises(ValueError, match="alvos ATS inválido"):
        alvos_ats.carregar()


def test_replay_trunca_por_bytes_e_remove_payload_vencido(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "DIRETORIO", tmp_path / "replay")
    monkeypatch.setattr(replay, "MAX_BYTES_POR_PAYLOAD", 11)
    monkeypatch.setattr(replay, "DIAS_RETENCAO", 1)
    monkeypatch.setattr(replay, "ATIVO", True)

    caminho = replay.gravar("html/falha", "vaga:á", "á" * 20, {"status": 500})
    payload = replay.carregar(caminho)
    assert payload["truncado"] is True
    assert payload["tamanho_original"] == 40
    assert len(payload["conteudo"].encode("utf-8")) <= 11
    assert caminho.parent.name == "html_falha"

    antigo = caminho.stat().st_mtime - 3 * 86_400
    os.utime(caminho, (antigo, antigo))
    assert replay.limpar() == 1
    assert not caminho.exists()


def test_replay_desligado_nao_grava(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "DIRETORIO", tmp_path / "replay")
    monkeypatch.setattr(replay, "ATIVO", False)
    assert replay.gravar("html", "vaga", "conteúdo") is None
    assert not replay.DIRETORIO.exists()
