"""API REST fina sobre o histórico de triagem (somente leitura + mudança de status).

Não reimplementa nada de `triagem/`: reusa `historico.py` como única fonte de
verdade, incluindo o lock de arquivo usado pela CLI, para que `triar` e a API
possam ser usados ao mesmo tempo sem corromper o `historico.json`.
"""

import os
from typing import Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from filelock import FileLock, Timeout
from pydantic import BaseModel

load_dotenv()

from triagem import historico  # noqa: E402  (após load_dotenv, ver aplicar_config_do_ambiente)

historico.aplicar_config_do_ambiente()

app = FastAPI(title="Triagem de Vagas API", version="1.0.0")

_ORIGENS_PADRAO = "http://localhost:5173,http://127.0.0.1:5173"
_origens = [
    o.strip()
    for o in os.environ.get("TRIAGEM_CORS_ORIGINS", _ORIGENS_PADRAO).split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origens,
    allow_methods=["GET", "PATCH"],
    allow_headers=["*"],
)

LOCK = FileLock(str(historico.caminho_lock()))

# Quanto o PATCH espera pelo lock antes de devolver 409. Configurável para que a
# espera não vire tempo morto na suíte de testes (e para dar uma alavanca a quem
# roda a CLI e a API lado a lado em máquina lenta).
LOCK_TIMEOUT_S = float(os.environ.get("TRIAGEM_API_LOCK_TIMEOUT", "5"))


class DimensaoResumo(BaseModel):
    nota: int
    justificativa: str


class VagaResumo(BaseModel):
    id: str
    empresa: str
    titulo: str
    status: historico.StatusVaga
    score_final: Optional[float]
    # regime/nivel_real/idioma_trabalho ficam como `str` (não Literal) de propósito:
    # embora triagem.schema.AnaliseVaga os tipe como Literal na análise, aqui eles vêm
    # de historico.json em disco, que pode ter registros de pipelines antigos/legados
    # sem esses campos. Apertar para Literal faria a API devolver 500 nesses casos em
    # vez da degradação graciosa atual (string vazia + fallback de label no frontend).
    regime: str
    localizacao: str
    nivel_real: str
    idioma_trabalho: str
    analisado_em: str
    link: Optional[str] = None
    stack_exigida: List[str] = []
    stack_desejavel: List[str] = []
    alertas: List[str] = []
    motivo_descarte: Optional[str] = None
    notas: Optional[Dict[str, DimensaoResumo]] = None


class AtualizarStatusPayload(BaseModel):
    status: str


def _carregar_historico() -> Dict[str, dict]:
    """`historico.carregar()` com o erro de leitura traduzido para HTTP.

    Um `historico.json` corrompido ou editado à mão faz `carregar()` levantar
    ValueError (com dica do .bak). Sem esta tradução o erro subia como 500 cru
    em toda rota de leitura, escondendo do frontend a única informação útil:
    que o problema é o arquivo, não a API. 503 porque é condição de servidor,
    transitória e resolvível — o cliente não tem o que corrigir na requisição.
    """
    try:
        return historico.carregar()
    except ValueError as e:
        raise HTTPException(503, str(e)) from e


def _para_resumo(vid: str, entrada: dict) -> VagaResumo:
    analise = entrada.get("analise") or {}
    notas_raw = analise.get("notas") or None
    return VagaResumo(
        id=vid,
        empresa=analise.get("empresa", "?"),
        titulo=analise.get("titulo_normalizado", "?"),
        status=entrada.get("status", "novo"),
        score_final=entrada.get("score_final"),
        regime=analise.get("regime", "indefinido"),
        localizacao=analise.get("localizacao", ""),
        nivel_real=analise.get("nivel_real", ""),
        idioma_trabalho=analise.get("idioma_trabalho", ""),
        analisado_em=entrada.get("analisado_em", ""),
        link=analise.get("link"),
        stack_exigida=analise.get("stack_exigida", []),
        stack_desejavel=analise.get("stack_desejavel", []),
        alertas=analise.get("alertas", []),
        motivo_descarte=analise.get("motivo_descarte"),
        notas=notas_raw,
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/stats")
def estatisticas():
    hist = _carregar_historico()
    contagem = {s: 0 for s in historico.STATUS_VALIDOS}
    for entrada in hist.values():
        status = entrada.get("status", "novo")
        contagem[status] = contagem.get(status, 0) + 1
    return {"total": len(hist), "por_status": contagem}


@app.get("/api/vagas", response_model=List[VagaResumo])
def listar_vagas(status: Optional[str] = None):
    hist = _carregar_historico()
    if status is not None and status not in historico.STATUS_VALIDOS:
        raise HTTPException(400, f"Status inválido: '{status}'.")
    resumos = [_para_resumo(vid, entrada) for vid, entrada in hist.items()]
    if status is not None:
        resumos = [r for r in resumos if r.status == status]
    resumos.sort(key=lambda r: (r.score_final is None, -(r.score_final or 0)))
    return resumos


@app.get("/api/vagas/{vaga_id}", response_model=VagaResumo)
def obter_vaga(vaga_id: str):
    hist = _carregar_historico()
    if vaga_id not in hist:
        raise HTTPException(404, f"Vaga '{vaga_id}' não encontrada.")
    return _para_resumo(vaga_id, hist[vaga_id])


@app.patch("/api/vagas/{vaga_id}/status", response_model=VagaResumo)
def atualizar_status(vaga_id: str, payload: AtualizarStatusPayload):
    try:
        with LOCK.acquire(timeout=LOCK_TIMEOUT_S):
            hist = _carregar_historico()
            vid = historico.atualizar_status(hist, vaga_id, payload.status)
            historico.salvar(hist)
    except Timeout as e:
        raise HTTPException(409, "Histórico em uso por outro processo, tente novamente.") from e
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return _para_resumo(vid, hist[vid])
