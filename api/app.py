"""API REST fina sobre o histórico de triagem (somente leitura + mudança de status).

Não reimplementa nada de `triagem/`: reusa `historico.py` como única fonte de
verdade, incluindo o lock de arquivo usado pela CLI, para que `triar` e a API
possam ser usados ao mesmo tempo sem corromper o `historico.json`.
"""

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "PATCH"],
    allow_headers=["*"],
)

LOCK = FileLock(str(historico.ARQUIVO) + ".lock")


class DimensaoResumo(BaseModel):
    nota: int
    justificativa: str


class VagaResumo(BaseModel):
    id: str
    empresa: str
    titulo: str
    status: str
    score_final: Optional[float]
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
    hist = historico.carregar()
    contagem = {s: 0 for s in historico.STATUS_VALIDOS}
    for entrada in hist.values():
        status = entrada.get("status", "novo")
        contagem[status] = contagem.get(status, 0) + 1
    return {"total": len(hist), "por_status": contagem}


@app.get("/api/vagas", response_model=List[VagaResumo])
def listar_vagas(status: Optional[str] = None):
    hist = historico.carregar()
    if status is not None and status not in historico.STATUS_VALIDOS:
        raise HTTPException(400, f"Status inválido: '{status}'.")
    resumos = [_para_resumo(vid, entrada) for vid, entrada in hist.items()]
    if status is not None:
        resumos = [r for r in resumos if r.status == status]
    resumos.sort(key=lambda r: (r.score_final is None, -(r.score_final or 0)))
    return resumos


@app.get("/api/vagas/{vaga_id}", response_model=VagaResumo)
def obter_vaga(vaga_id: str):
    hist = historico.carregar()
    if vaga_id not in hist:
        raise HTTPException(404, f"Vaga '{vaga_id}' não encontrada.")
    return _para_resumo(vaga_id, hist[vaga_id])


@app.patch("/api/vagas/{vaga_id}/status", response_model=VagaResumo)
def atualizar_status(vaga_id: str, payload: AtualizarStatusPayload):
    try:
        with LOCK.acquire(timeout=5):
            hist = historico.carregar()
            vid = historico.atualizar_status(hist, vaga_id, payload.status)
            historico.salvar(hist)
    except Timeout as e:
        raise HTTPException(409, "Histórico em uso por outro processo, tente novamente.") from e
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return _para_resumo(vid, hist[vid])
