import json

import pytest
from pydantic import ValidationError

from triagem import perfil_usuario
from triagem.analisador import system_prompt
from triagem.buscador import _cidades_aceitas, _termos_alvo


@pytest.fixture(autouse=True)
def perfil_isolado(tmp_path, monkeypatch):
    arquivo = tmp_path / "perfil.json"
    monkeypatch.setattr(perfil_usuario, "ARQUIVO", arquivo)
    monkeypatch.setattr(perfil_usuario, "_ATIVO", perfil_usuario.PerfilUsuario())
    system_prompt.cache_clear()
    yield arquivo
    system_prompt.cache_clear()


def test_perfil_personalizado_alimenta_busca_e_prompt():
    perfil = perfil_usuario.PerfilUsuario(
        nome="Ana",
        pais="Portugal",
        cidades_aceitas=["Lisboa", "Porto"],
        areas=["Data Engineering", "Python"],
        senioridades=["Pleno"],
        tecnologias=["Airflow", "dbt"],
        onboarding_concluido=True,
        consentimento_ia=True,
    )
    perfil_usuario.salvar(perfil)
    system_prompt.cache_clear()

    assert _termos_alvo() == ("data engineering", "python")
    assert _cidades_aceitas() == ("lisboa", "porto")
    assert "Ana" in system_prompt()
    assert "Lisboa, Porto" in system_prompt()
    assert "Data Engineering, Python" in system_prompt()
    assert "remotas em Portugal" in perfil.pedido_padrao()


def test_salvar_e_carregar_perfil_sem_perder_dados(perfil_isolado):
    perfil = perfil_usuario.PerfilUsuario(
        nome="Bruno", cidades_aceitas=["Recife"], areas=["QA"], senioridades=["Júnior"]
    )
    perfil_usuario.salvar(perfil)
    monkey = perfil_usuario.PerfilUsuario()
    perfil_usuario._ATIVO = monkey

    carregado = perfil_usuario.carregar()
    assert carregado.nome == "Bruno"
    assert carregado.areas == ["QA"]
    assert json.loads(perfil_isolado.read_text(encoding="utf-8"))["nome"] == "Bruno"


def test_perfil_rejeita_listas_obrigatorias_vazias():
    with pytest.raises(ValidationError, match="informe ao menos um valor"):
        perfil_usuario.PerfilUsuario(areas=[])


def test_perfil_rejeita_pesos_que_nao_somam_um():
    with pytest.raises(ValidationError, match="somar 1.0"):
        perfil_usuario.PerfilUsuario(pesos={
            "d1_crescimento": 0.5,
            "d2_regime_localizacao": 0.5,
            "d3_stack_fit": 0.5,
            "d4_ingles": 0.5,
            "d5_nivel_real": 0.5,
        })
