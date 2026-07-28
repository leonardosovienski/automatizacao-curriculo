"""Histórico persistente de vagas analisadas (dedup + acompanhamento de status)."""

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict

from .schema import VagaPontuada

ARQUIVO = Path(
    os.environ.get(
        "TRIAGEM_HISTORICO",
        Path(__file__).resolve().parent.parent / "historico.json",
    )
)

STATUS_VALIDOS = ["novo", "aplicado", "entrevista", "recusado", "descartada", "fechada"]

PADRAO = Path(__file__).resolve().parent.parent / "historico.json"


def aplicar_config_do_ambiente() -> None:
    """Re-resolve o caminho do histórico depois que o .env foi carregado.

    `ARQUIVO` é lido na importação do módulo, que acontece antes do `load_dotenv()`
    do CLI — sem esta chamada, `TRIAGEM_HISTORICO` definido no `.env` era ignorado.
    """
    global ARQUIVO
    ARQUIVO = Path(os.environ.get("TRIAGEM_HISTORICO") or PADRAO)


def gerar_id(texto: str) -> str:
    """ID estável da vaga: hash do texto normalizado (espaços/caixa ignorados)."""
    normalizado = " ".join(texto.lower().split())
    return hashlib.sha256(normalizado.encode("utf-8")).hexdigest()[:10]


def _dica_backup() -> str:
    """`salvar()` grava um .bak a cada escrita — de nada adianta se ninguém souber."""
    backup = ARQUIVO.with_suffix(f"{ARQUIVO.suffix}.bak")
    if backup.exists():
        return (f"\nHá um backup da gravação anterior em '{backup}'. "
                f"Para restaurar: copie-o sobre '{ARQUIVO.name}'.")
    return "\nNenhum backup (.bak) foi encontrado ao lado do arquivo."


def carregar() -> Dict[str, dict]:
    if not ARQUIVO.exists():
        return {}
    try:
        dados = json.loads(ARQUIVO.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"Não foi possível ler o histórico '{ARQUIVO}': {e}.{_dica_backup()}") from e
    if not isinstance(dados, dict):
        raise ValueError(
            f"Histórico inválido em '{ARQUIVO}': o conteúdo deve ser um objeto JSON."
            f"{_dica_backup()}"
        )
    return dados


def salvar(hist: Dict[str, dict]) -> None:
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    conteudo = json.dumps(hist, ensure_ascii=False, indent=2)
    fd, temporario = tempfile.mkstemp(
        prefix=f".{ARQUIVO.name}.", suffix=".tmp", dir=ARQUIVO.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as arquivo:
            arquivo.write(conteudo)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        if ARQUIVO.exists():
            shutil.copy2(ARQUIVO, ARQUIVO.with_suffix(f"{ARQUIVO.suffix}.bak"))
        os.replace(temporario, ARQUIVO)
    except BaseException:
        try:
            os.unlink(temporario)
        except FileNotFoundError:
            pass
        raise


def registrar_alias(hist: Dict[str, dict], vid: str, url: str) -> None:
    """Guarda a URL de origem alternativa da mesma vaga.

    A duplicata nunca é descartada em silêncio: o link do LinkedIn e o do site da
    empresa não são intercambiáveis para quem vai se candidatar, e a entrada
    vencedora precisa carregar os dois caminhos.
    """
    entrada = hist.get(vid)
    if entrada is None or not (url or "").strip():
        return
    aliases = entrada.setdefault("aliases", [])
    if url not in aliases:
        aliases.append(url)


def registrar(hist: Dict[str, dict], vaga: VagaPontuada, texto: str) -> None:
    """Insere/atualiza a vaga no histórico, preservando status manual em re-análises."""
    anterior = hist.get(vaga.id, {})
    if vaga.score_final is None:
        status = "descartada"
    elif anterior.get("status") in ("aplicado", "entrevista", "recusado"):
        status = anterior["status"]
    else:
        status = "novo"
    hist[vaga.id] = {
        "analisado_em": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "score_final": vaga.score_final,
        "texto": texto,
        "analise": vaga.analise.model_dump(),
    }


def buscar(hist: Dict[str, dict], prefixo: str) -> str:
    """Resolve um prefixo de ID para o ID completo, com erro claro se ambíguo."""
    candidatos = [k for k in hist if k.startswith(prefixo)]
    if not candidatos:
        raise KeyError(f"Nenhuma vaga no histórico com ID começando em '{prefixo}'.")
    if len(candidatos) > 1:
        raise KeyError(f"ID '{prefixo}' é ambíguo: {', '.join(candidatos)}.")
    return candidatos[0]


def atualizar_status(hist: Dict[str, dict], prefixo: str, novo_status: str) -> str:
    if novo_status not in STATUS_VALIDOS:
        raise ValueError(f"Status inválido: '{novo_status}'.")
    vid = buscar(hist, prefixo)
    hist[vid]["status"] = novo_status
    return vid


def marcar_fechadas_por_ats(
    hist: Dict[str, dict], provedor: str, token: str, ids_abertos: set[str]
) -> list[str]:
    """Fecha apenas vagas ainda novas que sumiram da API oficial do ATS.

    Candidaturas em andamento preservam seu status manual, mas recebem a marca de
    fechamento para que o histórico não apague o contexto da candidatura.
    """
    fechadas = []
    agora = datetime.now().isoformat(timespec="seconds")
    for ident, entrada in hist.items():
        try:
            origem = json.loads(entrada.get("texto") or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(origem, dict):
            continue
        if origem.get("ats_provedor") != provedor or origem.get("ats_token") != token:
            continue
        job_id = str(origem.get("ats_job_id") or "")
        if not job_id or job_id in ids_abertos:
            continue
        entrada["fechada_pelo_ats_em"] = agora
        if entrada.get("status") == "novo":
            entrada["status"] = "fechada"
            fechadas.append(ident)
    return fechadas
