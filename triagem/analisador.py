"""Chamada à API do Google Gemini com saída estruturada."""

import random
import time
from functools import lru_cache
from pathlib import Path

from google import genai
from google.genai import types

from .schema import AnaliseVaga

# Erros transitórios da API: cota por minuto, indisponibilidade, timeout de gateway.
CODIGOS_TRANSITORIOS = (
    "429", "500", "502", "503", "504", "resource_exhausted", "unavailable",
    "timeout", "timed out",
)
TENTATIVAS = 3

# Sem timeout explícito, uma conexão pendurada trava a thread para sempre e o
# ThreadPoolExecutor nunca fecha o lote (as_completed espera todos os futuros).
TIMEOUT_ANALISE_MS = 90_000
TIMEOUT_BUSCA_MS = 180_000

MODELOS = {
    "lite": "gemini-3.1-flash-lite",
    "flash": "gemini-3.5-flash",
}
MODELO_PADRAO = "lite"

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

# Schemas e prompts não mudam durante a execução: gerar/ler a cada vaga é I/O e
# CPU puro desperdício, e pesa mais ainda com --paralelo.
SCHEMA_ANALISE = AnaliseVaga.model_json_schema()


@lru_cache(maxsize=1)
def system_prompt() -> str:
    return (_PROMPTS_DIR / "system_prompt.md").read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def cv_prompt() -> str:
    return (_PROMPTS_DIR / "cv_prompt.md").read_text(encoding="utf-8")


def criar_cliente() -> genai.Client:
    """Cria o cliente usando GEMINI_API_KEY do ambiente."""
    return genai.Client()


def texto_da_resposta(response) -> str:
    """Extrai somente partes textuais, ignorando assinaturas internas de pensamento."""
    textos = []
    for candidate in response.candidates or []:
        content = candidate.content
        if not content:
            continue
        for part in content.parts or []:
            if part.text and not part.thought:
                textos.append(part.text)
    return "".join(textos)


def _e_transitorio(erro: Exception) -> bool:
    mensagem = f"{type(erro).__name__} {erro}".lower()
    return any(codigo in mensagem for codigo in CODIGOS_TRANSITORIOS)


def gerar_com_retentativa(client: genai.Client, **kwargs):
    """`generate_content` com backoff exponencial para 429/5xx.

    O free tier do Gemini estoura cota por minuto com facilidade quando o CLI roda
    com `--paralelo`; sem backoff a vaga era simplesmente perdida.
    """
    ultimo: Exception | None = None
    for tentativa in range(TENTATIVAS):
        try:
            return client.models.generate_content(**kwargs)
        except Exception as e:  # noqa: BLE001 — reavaliado por _e_transitorio
            ultimo = e
            if tentativa == TENTATIVAS - 1 or not _e_transitorio(e):
                raise
            time.sleep(2**tentativa + random.uniform(0, 0.5))
    raise ultimo  # pragma: no cover — inalcançável, o laço sempre retorna ou levanta


def analisar_vaga(
    client: genai.Client, texto_vaga: str, modelo: str = MODELO_PADRAO
) -> AnaliseVaga:
    response = gerar_com_retentativa(
        client,
        model=MODELOS[modelo],
        contents=f"Analise esta vaga:\n\n{texto_vaga}",
        config=types.GenerateContentConfig(
            system_instruction=system_prompt(),
            thinking_config=types.ThinkingConfig(
                include_thoughts=False,
                thinking_budget=0,
            ),
            response_mime_type="application/json",
            response_json_schema=SCHEMA_ANALISE,
            http_options=types.HttpOptions(timeout=TIMEOUT_ANALISE_MS),
        ),
    )
    texto = texto_da_resposta(response)
    if not texto:
        raise ValueError("Gemini não devolveu uma análise estruturada.")
    return AnaliseVaga.model_validate_json(texto)
