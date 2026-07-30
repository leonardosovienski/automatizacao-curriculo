"""Cache com TTL e circuit breaker por fonte de busca.

Duas funções distintas no mesmo arquivo de estado:

- **cache**: guarda o resultado bruto de cada fonte por (fonte, consulta). Evita
  repagar API em execuções seguidas e, quando a fonte falha, permite servir o
  resultado vencido com aviso explícito em vez de devolver nada.
- **circuito**: registra falhas consecutivas por fonte. A cota do Google Search
  Grounding fica esgotada por 24 h; sem isso, toda execução gasta uma chamada e
  ~2 s de latência para receber o mesmo 429.
"""

import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

PADRAO = Path(__file__).resolve().parent.parent / ".cache_busca.json"
ARQUIVO = Path(os.environ.get("TRIAGEM_CACHE") or PADRAO)

# Vagas novas aparecem ao longo do dia; 6 h mantém o resultado utilizável sem
# esconder anúncios recentes. O DDGS é volátil e barato, então expira antes.
TTL_SEGUNDOS = {
    "Jooble": 6 * 3600,
    "Adzuna": 6 * 3600,
    "Metabusca DDGS": 3600,
    "Google Search": 24 * 3600,
}
TTL_FALLBACK = 3600

FALHAS_PARA_ABRIR = 3
HORAS_CIRCUITO_ABERTO = 24

# O TTL só decide o que é servido; nada removia a entrada do disco. Buscas com
# pedidos diferentes geram chaves diferentes, então o arquivo crescia para sempre.
DIAS_RETENCAO = 30
VERSAO_CACHE = 2


def aplicar_config_do_ambiente() -> None:
    """Re-resolve o caminho depois do load_dotenv(), como no histórico."""
    global ARQUIVO
    ARQUIVO = Path(os.environ.get("TRIAGEM_CACHE") or PADRAO)


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _chave(fonte: str, consulta: str) -> str:
    digest = hashlib.sha256(consulta.encode("utf-8")).hexdigest()[:16]
    return f"v{VERSAO_CACHE}|{fonte}|{digest}"


def carregar() -> dict:
    if not ARQUIVO.exists():
        return {"entradas": {}, "circuitos": {}}
    try:
        dados = json.loads(ARQUIVO.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # Cache corrompido nunca deve quebrar a busca: ele é descartável.
        return {"entradas": {}, "circuitos": {}}
    if not isinstance(dados, dict):
        return {"entradas": {}, "circuitos": {}}
    if not isinstance(dados.get("entradas", {}), dict):
        return {"entradas": {}, "circuitos": {}}
    if not isinstance(dados.get("circuitos", {}), dict):
        return {"entradas": {}, "circuitos": {}}
    dados.setdefault("entradas", {})
    dados.setdefault("circuitos", {})
    dados["entradas"] = {
        chave: entrada
        for chave, entrada in dados["entradas"].items()
        if isinstance(chave, str)
        and isinstance(entrada, dict)
        and isinstance(entrada.get("gravado_em"), str)
        and "dados" in entrada
    }
    dados["circuitos"] = {
        chave: circuito
        for chave, circuito in dados["circuitos"].items()
        if isinstance(chave, str) and isinstance(circuito, dict)
    }
    podar(dados)
    return dados


def podar(estado: dict, dias: int = DIAS_RETENCAO) -> int:
    """Remove entradas velhas demais até para servir como fallback. Devolve quantas."""
    limite = dias * 86400
    velhas = [
        chave
        for chave, entrada in estado["entradas"].items()
        if (idade := _idade_segundos(entrada.get("gravado_em", ""))) is None or idade > limite
    ]
    for chave in velhas:
        del estado["entradas"][chave]
    return len(velhas)


def esvaziar(estado: dict) -> int:
    """Zera entradas e circuitos. Devolve quantas entradas foram removidas."""
    total = len(estado["entradas"])
    estado["entradas"] = {}
    estado["circuitos"] = {}
    return total


def salvar(estado: dict) -> None:
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    fd, temporario = tempfile.mkstemp(prefix=f".{ARQUIVO.name}.", suffix=".tmp", dir=ARQUIVO.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as arquivo:
            arquivo.write(json.dumps(estado, ensure_ascii=False, indent=2))
        os.replace(temporario, ARQUIVO)
    except BaseException:
        try:
            os.unlink(temporario)
        except FileNotFoundError:
            pass
        raise


def _idade_segundos(gravado_em: str) -> Optional[float]:
    try:
        momento = datetime.fromisoformat(gravado_em)
    except (TypeError, ValueError):
        return None
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    idade = (_agora() - momento).total_seconds()
    # Timestamp muito no futuro é estado inválido, não cache eternamente fresco.
    if idade < -300:
        return None
    return max(0.0, idade)


def obter(estado: dict, fonte: str, consulta: str) -> tuple[Optional[Any], Optional[float]]:
    """Devolve (dados, idade_em_segundos) apenas se ainda estiver dentro do TTL."""
    entrada = estado["entradas"].get(_chave(fonte, consulta))
    if not entrada:
        return None, None
    idade = _idade_segundos(entrada.get("gravado_em", ""))
    if idade is None or idade > TTL_SEGUNDOS.get(fonte, TTL_FALLBACK):
        return None, idade
    return entrada.get("dados"), idade


def obter_vencido(estado: dict, fonte: str, consulta: str) -> tuple[Optional[Any], Optional[float]]:
    """Devolve os dados mesmo fora do TTL — usado só quando a fonte falhou."""
    entrada = estado["entradas"].get(_chave(fonte, consulta))
    if not entrada:
        return None, None
    return entrada.get("dados"), _idade_segundos(entrada.get("gravado_em", ""))


def guardar(estado: dict, fonte: str, consulta: str, dados: Any) -> None:
    estado["entradas"][_chave(fonte, consulta)] = {
        "gravado_em": _agora().isoformat(timespec="seconds"),
        "dados": dados,
    }


def remover(estado: dict, fonte: str, consulta: str) -> None:
    estado["entradas"].pop(_chave(fonte, consulta), None)


def circuito_aberto(estado: dict, fonte: str) -> Optional[float]:
    """Horas que ainda faltam para o circuito fechar, ou None se está fechado."""
    circuito = estado["circuitos"].get(fonte)
    if not circuito or not circuito.get("aberto_ate"):
        return None
    try:
        ate = datetime.fromisoformat(circuito["aberto_ate"])
    except (TypeError, ValueError):
        return None
    if ate.tzinfo is None:
        ate = ate.replace(tzinfo=timezone.utc)
    restante = (ate - _agora()).total_seconds()
    return restante / 3600 if restante > 0 else None


def registrar_falha(estado: dict, fonte: str) -> bool:
    """Conta a falha e devolve True quando o circuito passou a ficar aberto."""
    circuito = estado["circuitos"].setdefault(fonte, {"falhas": 0, "aberto_ate": None})
    try:
        falhas = int(circuito.get("falhas", 0))
    except (TypeError, ValueError):
        falhas = 0
    circuito["falhas"] = falhas + 1
    if circuito["falhas"] >= FALHAS_PARA_ABRIR:
        circuito["aberto_ate"] = (
            _agora() + timedelta(hours=HORAS_CIRCUITO_ABERTO)
        ).isoformat(timespec="seconds")
        return True
    return False


def registrar_sucesso(estado: dict, fonte: str) -> None:
    estado["circuitos"][fonte] = {"falhas": 0, "aberto_ate": None}


def descrever_idade(segundos: Optional[float]) -> str:
    if segundos is None:
        return "idade desconhecida"
    if segundos < 90:
        return "agora há pouco"
    if segundos < 5400:
        return f"{int(segundos // 60)} min atrás"
    return f"{segundos / 3600:.0f} h atrás"
