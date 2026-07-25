"""Chamada à API do Google Gemini com saída estruturada."""

from pathlib import Path

from google import genai
from google.genai import types

from .schema import AnaliseVaga

MODELOS = {
    "lite": "gemini-3.1-flash-lite",
    "flash": "gemini-3.5-flash",
}
MODELO_PADRAO = "lite"

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def system_prompt() -> str:
    return (_PROMPTS_DIR / "system_prompt.md").read_text(encoding="utf-8")


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


def analisar_vaga(
    client: genai.Client, texto_vaga: str, modelo: str = MODELO_PADRAO
) -> AnaliseVaga:
    response = client.models.generate_content(
        model=MODELOS[modelo],
        contents=f"Analise esta vaga:\n\n{texto_vaga}",
        config=types.GenerateContentConfig(
            system_instruction=system_prompt(),
            thinking_config=types.ThinkingConfig(
                include_thoughts=False,
                thinking_budget=0,
            ),
            response_mime_type="application/json",
            response_json_schema=AnaliseVaga.model_json_schema(),
        ),
    )
    texto = texto_da_resposta(response)
    if not texto:
        raise ValueError("Gemini não devolveu uma análise estruturada.")
    return AnaliseVaga.model_validate_json(texto)
