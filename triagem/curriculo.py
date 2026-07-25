"""Gera material de candidatura sob medida usando o Google Gemini."""

import json
from pathlib import Path

from google import genai
from google.genai import types

from .analisador import MODELO_PADRAO, MODELOS, cv_prompt, texto_da_resposta

CV_BASE = Path(__file__).resolve().parent.parent / "perfil" / "cv_base.md"


def carregar_cv_base() -> str:
    if not CV_BASE.exists():
        raise FileNotFoundError(
            f"CV base não encontrado em {CV_BASE}. Crie o arquivo a partir do template do projeto."
        )
    return CV_BASE.read_text(encoding="utf-8")


def gerar_material(
    client: genai.Client,
    cv_base: str,
    texto_vaga: str,
    analise: dict,
    modelo: str = MODELO_PADRAO,
) -> str:
    conteudo = (
        f"## CV base do candidato\n\n{cv_base}\n\n"
        f"## Texto original da vaga\n\n{texto_vaga}\n\n"
        f"## Análise da triagem (JSON)\n\n{json.dumps(analise, ensure_ascii=False, indent=2)}"
    )
    response = client.models.generate_content(
        model=MODELOS[modelo],
        contents=conteudo,
        config=types.GenerateContentConfig(
            system_instruction=cv_prompt(),
            max_output_tokens=8000,
            thinking_config=types.ThinkingConfig(
                include_thoughts=False,
                thinking_budget=0,
            ),
        ),
    )
    texto = texto_da_resposta(response)
    if not texto:
        raise ValueError("Gemini não devolveu o material de candidatura.")
    return texto
