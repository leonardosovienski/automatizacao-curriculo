"""API SaaS multiusuário para triagem de vagas."""

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

load_dotenv()

from triagem import credenciais, historico, perfil_usuario  # noqa: E402

from .auth import (  # noqa: E402
    COOKIE_SESSAO,
    usuario_atual,
    verificar_senha,
)
from .auth_routes import cookie_seguro  # noqa: E402
from .auth_routes import router as auth_router  # noqa: E402
from .billing import (  # noqa: E402
    encerrar_cobranca_para_exclusao,
    reservar_busca,
    reservar_material,
)
from .billing import (  # noqa: E402
    router as billing_router,
)
from .config import configuracao_publica, producao, validar_configuracao  # noqa: E402
from .database import (  # noqa: E402
    Base,
    BuscaDB,
    PerfilDB,
    Usuario,
    VagaDB,
    criar_tabelas,
    sessao,
)
from .processamento import agendar  # noqa: E402
from .security import SecurityMiddleware, limitar_auth  # noqa: E402
from .worker import ACORDAR, iniciar_embutido  # noqa: E402


@asynccontextmanager
async def lifespan(_app: FastAPI):
    validar_configuracao()
    if not os.environ.get("DATABASE_URL"):
        criar_tabelas()
    worker = iniciar_embutido()
    yield
    if worker:
        worker[0].set()
        ACORDAR.set()


app = FastAPI(title="Triagem de Vagas API", version="2.1.0", lifespan=lifespan,
              docs_url=None if producao() else "/docs", redoc_url=None)

_ORIGENS_PADRAO = "" if producao() else "http://localhost:5173,http://127.0.0.1:5173"
_origens = [
    origem.strip()
    for origem in os.environ.get("TRIAGEM_CORS_ORIGINS", _ORIGENS_PADRAO).split(",")
    if origem.strip()
]
if os.environ.get("TRIAGEM_PUBLIC_URL"):
    _origens.append(os.environ["TRIAGEM_PUBLIC_URL"].rstrip("/"))
app.add_middleware(SecurityMiddleware, origens=_origens)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origens,
    allow_credentials=True,
    allow_methods=["GET", "PATCH", "PUT", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(billing_router)
app.include_router(auth_router)


@app.exception_handler(SQLAlchemyError)
async def erro_banco(_request: Request, erro: SQLAlchemyError):
    # Erros SQL não devem registrar currículo, senha ou parâmetros da consulta.
    logging.getLogger(__name__).error("Persistência indisponível: %s", type(erro).__name__)
    return JSONResponse(status_code=503, content={
        "detail": "O armazenamento está temporariamente indisponível. Tente novamente.",
    })


class DimensaoResumo(BaseModel):
    nota: int
    justificativa: str


class VagaResumo(BaseModel):
    id: str
    empresa: str
    titulo: str
    status: historico.StatusVaga
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


class CVPayload(BaseModel):
    conteudo: str = Field(max_length=50_000)


class ExcluirContaPayload(BaseModel):
    senha: str = Field(min_length=1, max_length=128)


class IniciarBuscaPayload(BaseModel):
    pedido: Optional[str] = Field(default=None, max_length=1000)
    limite: int = Field(default=10, ge=1, le=20)


class BuscaResposta(BaseModel):
    id: str
    estado: str
    progresso: int
    mensagem: str
    erro: Optional[str]
    encontradas: int
    pedido: str
    limite: int
    criada_em: datetime
    concluida_em: Optional[datetime]
    tipo: str = "busca"
    vaga_alvo_id: Optional[str] = None
    resultado: Optional[str] = None


def _perfil_do_usuario(db: Session, usuario: Usuario) -> PerfilDB:
    perfil = db.get(PerfilDB, usuario.id)
    if not perfil:
        perfil_padrao = perfil_usuario.PerfilUsuario(nome=usuario.email.split("@", 1)[0])
        perfil = PerfilDB(usuario_id=usuario.id, dados=perfil_padrao.model_dump(), cv_base="")
        db.add(perfil)
        db.commit()
        db.refresh(perfil)
    return perfil


def _para_resumo(vaga: VagaDB) -> VagaResumo:
    analise = vaga.analise or {}
    return VagaResumo(
        id=vaga.vaga_id,
        empresa=analise.get("empresa", "?"),
        titulo=analise.get("titulo_normalizado", "?"),
        status=vaga.status,
        score_final=vaga.score_final,
        regime=analise.get("regime", "indefinido"),
        localizacao=analise.get("localizacao", ""),
        nivel_real=analise.get("nivel_real", ""),
        idioma_trabalho=analise.get("idioma_trabalho", ""),
        analisado_em=vaga.analisado_em,
        link=analise.get("link"),
        stack_exigida=analise.get("stack_exigida", []),
        stack_desejavel=analise.get("stack_desejavel", []),
        alertas=analise.get("alertas", []),
        motivo_descarte=analise.get("motivo_descarte"),
        notas=analise.get("notas") or None,
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready(db: Session = Depends(sessao)):
    try:
        db.execute(text("SELECT 1"))
        db.execute(select(BuscaDB.id, BuscaDB.worker_token).limit(1))
    except Exception as erro:
        raise HTTPException(503, "Banco de dados indisponível ou migrações pendentes.") from erro
    return {"status": "ready"}


@app.get("/api/config/publico")
def config_publico():
    return configuracao_publica()


@app.get("/api/auth/me")
def me(usuario: Usuario = Depends(usuario_atual)):
    return {"id": usuario.id, "email": usuario.email}


@app.get("/api/auth/exportar")
def exportar_dados(
    usuario: Usuario = Depends(usuario_atual), db: Session = Depends(sessao)
):
    perfil = _perfil_do_usuario(db, usuario)
    vagas = list(db.scalars(select(VagaDB).where(VagaDB.usuario_id == usuario.id)).all())
    adicionais = {}
    for nome in ("buscas", "assinaturas", "aceites_termos", "uso_mensal"):
        tabela = Base.metadata.tables.get(nome)
        if tabela is not None:
            adicionais[nome] = [dict(linha) for linha in db.execute(
                select(tabela).where(tabela.c.usuario_id == usuario.id)
            ).mappings()]
    for busca in adicionais.get("buscas", []):
        busca.pop("worker_token", None)
    return {
        "usuario": {"id": usuario.id, "email": usuario.email, "criado_em": usuario.criado_em},
        "perfil": perfil.dados,
        "cv_base": perfil.cv_base,
        **adicionais,
        "vagas": [
            {
                "id": vaga.vaga_id, "status": vaga.status, "score_final": vaga.score_final,
                "analisado_em": vaga.analisado_em, "texto": vaga.texto,
                "analise": vaga.analise, "aliases": vaga.aliases,
            }
            for vaga in vagas
        ],
    }


@app.delete("/api/auth/me", status_code=204)
def excluir_conta(
    payload: ExcluirContaPayload,
    request: Request,
    response: Response,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(sessao),
):
    limitar_auth(request, db, "excluir", usuario.email)
    if not verificar_senha(payload.senha, usuario.senha_hash):
        raise HTTPException(403, "Senha incorreta.")
    # A mesma linha é travada pelo worker antes de persistir resultados.
    db.execute(select(Usuario.id).where(Usuario.id == usuario.id).with_for_update())
    encerrar_cobranca_para_exclusao(db, usuario)
    # Exclusão explícita também em SQLite, inclusive tabelas sem relationship ORM.
    for tabela in reversed(Base.metadata.sorted_tables):
        if tabela.name != "usuarios" and "usuario_id" in tabela.c:
            db.execute(delete(tabela).where(tabela.c.usuario_id == usuario.id))
    db.execute(delete(Usuario).where(Usuario.id == usuario.id))
    db.commit()
    response.delete_cookie(COOKIE_SESSAO, httponly=True, secure=cookie_seguro(), samesite="lax")


@app.get("/api/onboarding")
def estado_onboarding(
    usuario: Usuario = Depends(usuario_atual), db: Session = Depends(sessao)
):
    registro = _perfil_do_usuario(db, usuario)
    perfil = perfil_usuario.PerfilUsuario.model_validate(registro.dados)
    return {
        "concluido": perfil.onboarding_concluido,
        "consentimento_ia": perfil.consentimento_ia,
        "cv_configurado": bool(registro.cv_base.strip()),
    }


@app.get("/api/perfil", response_model=perfil_usuario.PerfilUsuario)
def obter_perfil(usuario: Usuario = Depends(usuario_atual), db: Session = Depends(sessao)):
    return perfil_usuario.PerfilUsuario.model_validate(_perfil_do_usuario(db, usuario).dados)


@app.put("/api/perfil", response_model=perfil_usuario.PerfilUsuario)
def atualizar_perfil(
    perfil: perfil_usuario.PerfilUsuario,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(sessao),
):
    db.execute(update(Usuario).where(Usuario.id == usuario.id).values(id=Usuario.id))
    registro = _perfil_do_usuario(db, usuario)
    registro.dados = perfil.model_dump()
    if not perfil.consentimento_ia:
        db.execute(update(BuscaDB).where(
            BuscaDB.usuario_id == usuario.id, BuscaDB.estado.in_(["pendente", "processando"]),
        ).values(estado="falhou", worker_token=None, progresso=100,
                 mensagem="Processamento interrompido após revogação do consentimento de IA.",
                 erro="Autorize o uso de IA no perfil para iniciar um novo processamento."))
    db.commit()
    return perfil


@app.get("/api/cv")
def obter_cv(usuario: Usuario = Depends(usuario_atual), db: Session = Depends(sessao)):
    return {"conteudo": _perfil_do_usuario(db, usuario).cv_base}


@app.put("/api/cv")
def atualizar_cv(
    payload: CVPayload,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(sessao),
):
    if not payload.conteudo.strip():
        raise HTTPException(400, "O CV base não pode ficar vazio.")
    if len(payload.conteudo.encode("utf-8")) > 500_000:
        raise HTTPException(400, "O CV base excede o limite de 500 KB.")
    registro = _perfil_do_usuario(db, usuario)
    registro.cv_base = payload.conteudo
    db.commit()
    return {"salvo": True}


def _busca_resposta(busca: BuscaDB) -> BuscaResposta:
    return BuscaResposta(
        id=busca.id, estado=busca.estado, progresso=busca.progresso,
        mensagem=busca.mensagem, erro=busca.erro, encontradas=busca.encontradas,
        pedido=busca.pedido, limite=busca.limite,
        criada_em=busca.criada_em, concluida_em=busca.concluida_em,
        tipo=busca.tipo, vaga_alvo_id=busca.vaga_alvo_id, resultado=busca.resultado,
    )


@app.post("/api/buscas", response_model=BuscaResposta, status_code=202)
def iniciar_busca(
    payload: IniciarBuscaPayload,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(sessao),
):
    credenciais.carregar_no_ambiente()
    if not os.environ.get("GEMINI_API_KEY"):
        raise HTTPException(503, "A integração de análise ainda não foi configurada pelo operador.")
    perfil_db = _perfil_do_usuario(db, usuario)
    perfil = perfil_usuario.PerfilUsuario.model_validate(perfil_db.dados)
    if (
        not perfil.onboarding_concluido
        or not perfil.consentimento_ia
        or not perfil_db.cv_base.strip()
    ):
        raise HTTPException(409, "Complete o perfil e o currículo antes de buscar vagas.")
    pedido = (payload.pedido or perfil.pedido_padrao()).strip()
    if not pedido:
        raise HTTPException(400, "Informe o tipo de vaga desejado.")
    from triagem.curriculo import preparar_cv_para_ia
    try:
        if not preparar_cv_para_ia(perfil_db.cv_base).strip():
            raise ValueError("O currículo precisa ter conteúdo público para análise.")
    except ValueError as erro:
        raise HTTPException(422, str(erro)) from erro
    reservar_busca(db, usuario, payload.limite)
    busca = BuscaDB(usuario_id=usuario.id, pedido=pedido, limite=payload.limite)
    db.add(busca)
    try:
        db.commit()
    except IntegrityError as erro:
        db.rollback()
        raise HTTPException(409, "Já existe uma busca em andamento para esta conta.") from erro
    db.refresh(busca)
    agendar(busca.id)
    return _busca_resposta(busca)


@app.get("/api/buscas/atual", response_model=Optional[BuscaResposta])
def busca_atual(usuario: Usuario = Depends(usuario_atual), db: Session = Depends(sessao)):
    busca = db.scalar(
        select(BuscaDB).where(BuscaDB.usuario_id == usuario.id)
        .where(BuscaDB.tipo == "busca")
        .order_by(BuscaDB.criada_em.desc()).limit(1)
    )
    return _busca_resposta(busca) if busca else None


@app.get("/api/buscas/{busca_id}", response_model=BuscaResposta)
def obter_busca(
    busca_id: str, usuario: Usuario = Depends(usuario_atual), db: Session = Depends(sessao)
):
    busca = db.scalar(select(BuscaDB).where(
        BuscaDB.id == busca_id, BuscaDB.usuario_id == usuario.id
    ))
    if not busca:
        raise HTTPException(404, "Busca não encontrada.")
    return _busca_resposta(busca)


@app.get("/api/stats")
def estatisticas(usuario: Usuario = Depends(usuario_atual), db: Session = Depends(sessao)):
    vagas = db.scalars(select(VagaDB).where(VagaDB.usuario_id == usuario.id)).all()
    contagem = {s: 0 for s in historico.STATUS_VALIDOS}
    for vaga in vagas:
        contagem[vaga.status] = contagem.get(vaga.status, 0) + 1
    return {"total": len(vagas), "por_status": contagem}


@app.get("/api/vagas", response_model=List[VagaResumo])
def listar_vagas(
    status_vaga: Optional[str] = None,
    status: Optional[str] = None,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(sessao),
):
    filtro = status_vaga or status
    if filtro is not None and filtro not in historico.STATUS_VALIDOS:
        raise HTTPException(400, f"Status inválido: '{filtro}'.")
    consulta = select(VagaDB).where(VagaDB.usuario_id == usuario.id)
    if filtro:
        consulta = consulta.where(VagaDB.status == filtro)
    vagas = list(db.scalars(consulta).all())
    vagas.sort(key=lambda vaga: (vaga.score_final is None, -(vaga.score_final or 0)))
    return [_para_resumo(vaga) for vaga in vagas]


def _resolver_vaga(db: Session, usuario: Usuario, prefixo: str) -> VagaDB:
    vagas = list(db.scalars(select(VagaDB).where(VagaDB.usuario_id == usuario.id)).all())
    candidatas = [vaga for vaga in vagas if vaga.vaga_id.startswith(prefixo)]
    if not candidatas:
        raise HTTPException(404, f"Vaga '{prefixo}' não encontrada.")
    if len(candidatas) > 1:
        raise HTTPException(409, f"ID '{prefixo}' é ambíguo.")
    return candidatas[0]


@app.get("/api/vagas/{vaga_id}", response_model=VagaResumo)
def obter_vaga(
    vaga_id: str, usuario: Usuario = Depends(usuario_atual), db: Session = Depends(sessao)
):
    return _para_resumo(_resolver_vaga(db, usuario, vaga_id))


@app.patch("/api/vagas/{vaga_id}/status", response_model=VagaResumo)
def atualizar_status(
    vaga_id: str,
    payload: AtualizarStatusPayload,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(sessao),
):
    if payload.status not in historico.STATUS_VALIDOS:
        raise HTTPException(400, f"Status inválido: '{payload.status}'.")
    vaga = _resolver_vaga(db, usuario, vaga_id)
    vaga.status = payload.status
    db.commit()
    return _para_resumo(vaga)


@app.post("/api/vagas/{vaga_id}/material", response_model=BuscaResposta, status_code=202)
def iniciar_material(
    vaga_id: str, usuario: Usuario = Depends(usuario_atual), db: Session = Depends(sessao),
):
    from triagem.curriculo import _preparar_evidencias, preparar_cv_para_ia
    if not os.environ.get("GEMINI_API_KEY"):
        raise HTTPException(503, "A integração de análise ainda não foi configurada pelo operador.")
    vaga = _resolver_vaga(db, usuario, vaga_id)
    registro = _perfil_do_usuario(db, usuario)
    perfil = perfil_usuario.PerfilUsuario.model_validate(registro.dados)
    if not perfil.consentimento_ia or not perfil.onboarding_concluido:
        raise HTTPException(409, "Complete o perfil e autorize o uso de IA antes de continuar.")
    try:
        _preparar_evidencias(preparar_cv_para_ia(registro.cv_base))
    except ValueError as erro:
        raise HTTPException(422, str(erro)) from erro
    reservar_material(db, usuario)
    busca = BuscaDB(usuario_id=usuario.id, pedido="Material de candidatura", limite=1,
                    tipo="material", vaga_alvo_id=vaga.vaga_id)
    db.add(busca)
    try:
        db.commit()
    except IntegrityError as erro:
        db.rollback()
        raise HTTPException(409, "Já existe um processamento em andamento para esta conta.") from erro
    db.refresh(busca)
    agendar(busca.id)
    return _busca_resposta(busca)


@app.get("/api/vagas/{vaga_id}/material", response_model=Optional[BuscaResposta])
def obter_material(
    vaga_id: str, usuario: Usuario = Depends(usuario_atual), db: Session = Depends(sessao),
):
    vaga = _resolver_vaga(db, usuario, vaga_id)
    busca = db.scalar(select(BuscaDB).where(
        BuscaDB.usuario_id == usuario.id, BuscaDB.tipo == "material",
        BuscaDB.vaga_alvo_id == vaga.vaga_id,
    ).order_by(BuscaDB.criada_em.desc()).limit(1))
    return _busca_resposta(busca) if busca else None


# Docker entrega SPA e API na mesma origem; rotas desconhecidas da API mantêm 404.
_frontend = Path(os.environ.get("TRIAGEM_FRONTEND_DIST", Path(__file__).resolve().parents[1] / "frontend/dist"))
if (_frontend / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=_frontend / "assets"), name="assets")

    @app.get("/{caminho:path}", include_in_schema=False)
    def frontend(caminho: str):
        if caminho == "api" or caminho.startswith(("api/", "billing/")):
            raise HTTPException(404, "Recurso não encontrado.")
        arquivo = (_frontend / caminho).resolve()
        if caminho and arquivo.is_relative_to(_frontend.resolve()) and arquivo.is_file():
            return FileResponse(arquivo)
        return FileResponse(_frontend / "index.html", headers={"Cache-Control": "no-cache"})
