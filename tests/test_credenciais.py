import os

import pytest

from triagem import credenciais


def test_salvar_usa_keyring_e_injeta_no_ambiente(monkeypatch):
    chamadas = []
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(
        credenciais.keyring, "set_password", lambda servico, conta, valor: chamadas.append((servico, conta, valor))
    )
    credenciais.salvar("gemini", "abc123")
    assert chamadas == [(credenciais.SERVICO, "gemini", "abc123")]
    assert os.environ["GEMINI_API_KEY"] == "abc123"


def test_obter_prefere_variavel_de_ambiente(monkeypatch):
    monkeypatch.setenv("JOOBLE_API_KEY", "do-ambiente")
    monkeypatch.setattr(credenciais.keyring, "get_password", lambda *args: pytest.fail("não deve ler cofre"))
    assert credenciais.obter("jooble") == "do-ambiente"


def test_status_retorna_apenas_booleanos(monkeypatch):
    monkeypatch.setattr(credenciais, "obter", lambda provedor: "segredo" if provedor == "gemini" else None)
    assert credenciais.status()["gemini"] is True
    assert set(credenciais.status().values()) <= {True, False}
