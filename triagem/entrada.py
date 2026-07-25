"""Carrega vagas do input (JSON ou texto livre) e normaliza para texto por vaga."""

import json
from pathlib import Path
from typing import List

SEPARADOR_TEXTO = "\n---\n"


def carregar_vagas(conteudo: str) -> List[str]:
    """Recebe o conteúdo bruto e devolve uma lista de vagas em texto.

    - JSON (lista de objetos ou objeto único): cada item vira um bloco de texto
      com os campos preservados, para o modelo extrair.
    - Texto livre: vagas separadas por uma linha contendo apenas '---';
      sem separador, o conteúdo inteiro é uma vaga só.
    """
    conteudo = conteudo.strip()
    if not conteudo:
        raise ValueError("Input vazio. Envie as vagas em JSON ou texto livre.")

    if conteudo.startswith(("[", "{")):
        try:
            dados = json.loads(conteudo)
        except json.JSONDecodeError as e:
            raise ValueError(f"Input parece JSON mas está malformado: {e}") from e
        if isinstance(dados, dict):
            dados = [dados]
        if not isinstance(dados, list) or not all(isinstance(v, dict) for v in dados):
            raise ValueError("JSON deve ser um objeto ou uma lista de objetos de vaga.")
        vagas = [_vaga_json_para_texto(v) for v in dados]
        if not vagas or any(not vaga.strip() for vaga in vagas):
            raise ValueError("JSON não contém nenhuma vaga com dados.")
        return vagas

    # Aceita CRLF e espaços na linha separadora.
    import re
    blocos = [b.strip() for b in re.split(r"\r?\n\s*---\s*\r?\n", conteudo)]
    return [b for b in blocos if b]


def carregar_arquivo(caminho: str) -> List[str]:
    return carregar_vagas(Path(caminho).read_text(encoding="utf-8"))


def _vaga_json_para_texto(vaga: dict) -> str:
    linhas = []
    for campo in ("titulo", "empresa", "link", "origem"):
        if vaga.get(campo):
            linhas.append(f"{campo}: {vaga[campo]}")
    descricao = vaga.get("descricao", "")
    if descricao:
        linhas.append(f"descricao:\n{descricao}")
    # Campos extras que não estão no formato esperado também são repassados
    extras = {k: v for k, v in vaga.items() if k not in ("titulo", "empresa", "link", "origem", "descricao")}
    for k, v in extras.items():
        linhas.append(f"{k}: {v}")
    return "\n".join(linhas)
