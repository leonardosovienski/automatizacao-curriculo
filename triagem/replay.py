"""Persistência de payloads brutos para reprodução determinística de falhas.

O problema medido em 2026-07-27: a vaga da AvePoint entrou com `localizacao:
"Remoto"` inventada e virou a recomendação #1 — o anúncio real é presencial no
Vietnã. A correção foi escrita e testada, mas **não pôde ser validada em produção**,
porque o Google Search Grounding devolve resultados diferentes a cada chamada e a
vaga nunca mais voltou.

Um sistema que não consegue reproduzir a própria falha não tem como provar que a
consertou. Isto guarda o que faltava:

- o **blob de texto livre** do grounding, que hoje vai direto para a normalização
  sem passar pelo cache (só o fallback DDGS é cacheado);
- o **HTML cru** da página, mas **apenas nos caminhos de falha e descarte** — é lá
  que o replay importa, e uma home page como a da Solides sozinha tem 397 KB.

Teto por arquivo e expiração por idade, porque disco cheio é um jeito novo de
quebrar a busca.
"""

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PADRAO = Path.cwd() / ".replay"
DIRETORIO = Path(os.environ.get("TRIAGEM_REPLAY") or PADRAO)

# Teto por payload. Acima disto o material é truncado: o começo da página já basta
# para reproduzir extração de JSON-LD e de descrição.
MAX_BYTES_POR_PAYLOAD = 512 * 1024
DIAS_RETENCAO = 14
# Teto do diretório inteiro. Ultrapassado, os mais antigos saem primeiro.
MAX_BYTES_TOTAL = 64 * 1024 * 1024

ATIVO = os.environ.get("TRIAGEM_REPLAY_DESLIGADO", "").strip().lower() not in ("1", "true", "sim")


def aplicar_config_do_ambiente() -> None:
    global DIRETORIO, ATIVO
    DIRETORIO = Path(os.environ.get("TRIAGEM_REPLAY") or PADRAO)
    ATIVO = os.environ.get("TRIAGEM_REPLAY_DESLIGADO", "").strip().lower() not in (
        "1", "true", "sim",
    )


def _nome_seguro(rotulo: str) -> str:
    limpo = re.sub(r"[^A-Za-z0-9._-]+", "_", rotulo or "sem_rotulo")
    return limpo[:60].strip("_") or "sem_rotulo"


def gravar(categoria: str, rotulo: str, conteudo: str, meta: Optional[dict] = None) -> Optional[Path]:
    """Guarda um payload bruto. Devolve o caminho, ou None quando desligado/falho.

    Nunca levanta: falha ao gravar diagnóstico não pode derrubar a busca.
    """
    if not ATIVO or not (conteudo or "").strip():
        return None
    try:
        destino = DIRETORIO / _nome_seguro(categoria)
        destino.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256((rotulo or "").encode("utf-8")).hexdigest()[:10]
        carimbo = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        arquivo = destino / f"{carimbo}_{_nome_seguro(rotulo)}_{digest}.json"
        bruto = conteudo.encode("utf-8")
        truncado = len(bruto) > MAX_BYTES_POR_PAYLOAD
        corpo = bruto[:MAX_BYTES_POR_PAYLOAD].decode("utf-8", errors="ignore")
        arquivo.write_text(
            json.dumps(
                {
                    "rotulo": rotulo,
                    "gravado_em": datetime.now(timezone.utc).isoformat(),
                    "truncado": truncado,
                    "tamanho_original": len(bruto),
                    "meta": meta or {},
                    "conteudo": corpo,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return arquivo
    except OSError:
        return None


def limpar() -> int:
    """Remove payloads vencidos e, se ainda estourar o teto, os mais antigos."""
    if not DIRETORIO.exists():
        return 0
    removidos = 0
    limite = time.time() - DIAS_RETENCAO * 86400
    arquivos = []
    for arquivo in DIRETORIO.rglob("*.json"):
        try:
            estado = arquivo.stat()
        except OSError:
            continue
        if estado.st_mtime < limite:
            try:
                arquivo.unlink()
                removidos += 1
            except OSError:
                pass
            continue
        arquivos.append((estado.st_mtime, estado.st_size, arquivo))

    total = sum(tamanho for _, tamanho, _ in arquivos)
    for _, tamanho, arquivo in sorted(arquivos):
        if total <= MAX_BYTES_TOTAL:
            break
        try:
            arquivo.unlink()
            total -= tamanho
            removidos += 1
        except OSError:
            pass
    return removidos


def carregar(caminho: Path) -> Optional[dict]:
    """Lê um payload gravado, para reexecutar a extração sobre ele."""
    try:
        return json.loads(Path(caminho).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
