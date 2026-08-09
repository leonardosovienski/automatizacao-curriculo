"""Credenciais locais armazenadas no cofre do sistema operacional."""

import os
from typing import Literal

import httpx
import keyring
from keyring.errors import KeyringError

SERVICO = "triagem-vagas"
Provedor = Literal["gemini", "jooble", "adzuna_app_id", "adzuna_api_key"]
VARIAVEIS: dict[Provedor, str] = {
    "gemini": "GEMINI_API_KEY",
    "jooble": "JOOBLE_API_KEY",
    "adzuna_app_id": "ADZUNA_APP_ID",
    "adzuna_api_key": "ADZUNA_API_KEY",
}


def obter(provedor: Provedor) -> str | None:
    variavel = VARIAVEIS[provedor]
    if os.environ.get(variavel):
        return os.environ[variavel]
    try:
        return keyring.get_password(SERVICO, provedor)
    except KeyringError:
        return None


def salvar(provedor: Provedor, valor: str) -> None:
    valor = valor.strip()
    if not valor:
        raise ValueError("credencial vazia")
    try:
        keyring.set_password(SERVICO, provedor, valor)
    except KeyringError as e:
        raise RuntimeError(
            "O cofre seguro do sistema operacional não está disponível nesta sessão."
        ) from e
    os.environ[VARIAVEIS[provedor]] = valor


def carregar_no_ambiente() -> None:
    for provedor, variavel in VARIAVEIS.items():
        if variavel not in os.environ:
            valor = obter(provedor)
            if valor:
                os.environ[variavel] = valor


def status() -> dict[str, bool]:
    return {provedor: bool(obter(provedor)) for provedor in VARIAVEIS}


def validar(provedor: Provedor, valor: str, *, complementar: str | None = None) -> None:
    """Valida a credencial sem persistir e sem incluir o segredo no erro."""
    try:
        if provedor == "gemini":
            resposta = httpx.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                params={"key": valor}, timeout=15,
            )
        elif provedor == "jooble":
            resposta = httpx.post(
                f"https://jooble.org/api/{valor}", json={"keywords": "test", "page": 1},
                timeout=15,
            )
        elif provedor in {"adzuna_app_id", "adzuna_api_key"}:
            app_id = valor if provedor == "adzuna_app_id" else obter("adzuna_app_id")
            api_key = valor if provedor == "adzuna_api_key" else complementar or obter("adzuna_api_key")
            if not app_id or not api_key:
                raise ValueError("Adzuna exige App ID e API Key")
            resposta = httpx.get(
                "https://api.adzuna.com/v1/api/jobs/br/search/1",
                params={"app_id": app_id, "app_key": api_key, "results_per_page": 1},
                timeout=15,
            )
        else:  # pragma: no cover - Literal protege chamadores tipados
            raise ValueError("provedor desconhecido")
    except httpx.RequestError as e:
        raise ValueError(f"não foi possível contatar o provedor: {type(e).__name__}") from e
    if resposta.status_code >= 400:
        raise ValueError(f"credencial recusada pelo provedor (HTTP {resposta.status_code})")
