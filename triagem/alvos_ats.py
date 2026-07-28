"""Estado local dos ATS descobertos passivamente durante a busca."""

import json
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path

PADRAO = Path(__file__).resolve().parent.parent / "alvos_ats.json"
ARQUIVO = Path(os.environ.get("TRIAGEM_ALVOS_ATS") or PADRAO)
_TRAVA = threading.RLock()


def aplicar_config_do_ambiente() -> None:
    global ARQUIVO
    ARQUIVO = Path(os.environ.get("TRIAGEM_ALVOS_ATS") or PADRAO)


def carregar() -> dict:
    if not ARQUIVO.exists():
        return {"alvos": {}}
    dados = json.loads(ARQUIVO.read_text(encoding="utf-8"))
    if not isinstance(dados, dict) or not isinstance(dados.get("alvos", {}), dict):
        raise ValueError(f"Arquivo de alvos ATS inválido: '{ARQUIVO}'.")
    dados.setdefault("alvos", {})
    return dados


def salvar(estado: dict) -> None:
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    fd, temporario = tempfile.mkstemp(prefix=f".{ARQUIVO.name}.", suffix=".tmp", dir=ARQUIVO.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as arquivo:
            json.dump(estado, arquivo, ensure_ascii=False, indent=2)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        os.replace(temporario, ARQUIVO)
    except BaseException:
        try:
            os.unlink(temporario)
        except FileNotFoundError:
            pass
        raise


def registrar(provedor: str, token: str) -> None:
    """Registra um alvo sem deixar falha de disco derrubar o worker de rede."""
    if not provedor or not token:
        return
    with _TRAVA:
        try:
            estado = carregar()
            chave = f"{provedor}:{token}"
            agora = datetime.now().isoformat(timespec="seconds")
            alvo = estado["alvos"].setdefault(
                chave,
                {"provedor": provedor, "token": token, "status": "ativo", "descoberto_em": agora},
            )
            alvo["visto_em"] = agora
            salvar(estado)
        except (OSError, ValueError, json.JSONDecodeError):
            return


def ativos(estado: dict) -> list[dict]:
    return [alvo for alvo in estado["alvos"].values() if alvo.get("status") == "ativo"]


def aplicar_resultado(estado: dict, resultado) -> None:
    alvo = estado["alvos"].get(f"{resultado.provedor}:{resultado.token}")
    if alvo is None:
        return
    agora = datetime.now().isoformat(timespec="seconds")
    alvo["ultimo_sync_em"] = agora
    if resultado.estado == "inativo":
        alvo["status"] = "inativo"
        alvo["inativado_em"] = agora
    elif resultado.estado == "ativo":
        alvo["status"] = "ativo"
        alvo.pop("ultima_falha", None)
    else:
        alvo["ultima_falha"] = resultado.erro or "falha transitória"
