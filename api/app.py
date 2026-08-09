"""API SaaS multiusuário para triagem de vagas."""

import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, List, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

load_dotenv()

from triagem import credenciais, historico, perfil_usuario  # noqa: E402

from .auth import (  # noqa: E402
    COOKIE_SESSAO,
    criar_token,
    hash_senha,
    usuario_atual,
    verificar_senha,
)
from .database import (  # noqa: E402
    BuscaDB,
    PerfilDB,
    Usuario,
    VagaDB,
    criar_tabelas,
    sessao,
)
from .processamento import agendar  # noqa: E402


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not os.environ.get("DATABASE_URL"):
        criar_tabelas()
    yield


app = FastAPI(title="Triagem de Vagas API", version="2.0.0", lifespan=lifespan)

_ORIGENS_PADRAO = "http://localhost:5173,http://127.0.0.1:5173"
_origens = [
    origem.strip()
    for origem in os.environ.get("TRIAGEM_CORS_ORIGINS", _ORIGENS_PADRAO).split(",")
    if origem.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origens,
    allow_credentials=True,
    allow_methods=["GET", "PATCH", "PUT", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


class CadastroPayload(BaseModel):
    email: EmailStr
    senha: str = Field(min_length=10, max_length=128)


class LoginPayload(BaseModel):
    email: EmailStr
    senha: str


class SessaoResposta(BaseModel):
    usuario: dict


def _gravar_cookie(response: Response, usuario: Usuario) -> None:
    response.set_cookie(
        COOKIE_SESSAO, criar_token(usuario), httponly=True,
        secure=bool(os.environ.get("DATABASE_URL")), samesite="lax",
        max_age=int(os.environ.get("TRIAGEM_TOKEN_MINUTES", "60")) * 60,
    )


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
    conteudo: str


class ExcluirContaPayload(BaseModel):
    senha: str


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


@app.post("/api/auth/cadastro", response_model=SessaoResposta, status_code=status.HTTP_201_CREATED)
def cadastro(payload: CadastroPayload, response: Response, db: Session = Depends(sessao)):
    usuario = Usuario(email=str(payload.email).lower(), senha_hash=hash_senha(payload.senha))
    db.add(usuario)
    try:
        db.flush()
        perfil = perfil_usuario.PerfilUsuario(nome=usuario.email.split("@", 1)[0])
        db.add(PerfilDB(usuario_id=usuario.id, dados=perfil.model_dump(), cv_base=""))
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(409, "Já existe uma conta com este e-mail.") from e
    _gravar_cookie(response, usuario)
    return SessaoResposta(usuario={"id": usuario.id, "email": usuario.email})


@app.post("/api/auth/login", response_model=SessaoResposta)
def login(payload: LoginPayload, response: Response, db: Session = Depends(sessao)):
    usuario = db.scalar(select(Usuario).where(Usuario.email == str(payload.email).lower()))
    if not usuario or not verificar_senha(payload.senha, usuario.senha_hash):
        raise HTTPException(401, "E-mail ou senha inválidos.")
    _gravar_cookie(response, usuario)
    return SessaoResposta(usuario={"id": usuario.id, "email": usuario.email})


@app.post("/api/auth/logout", status_code=204)
def logout(response: Response):
    response.delete_cookie(COOKIE_SESSAO, httponly=True, samesite="lax")


@app.get("/api/auth/me")
def me(usuario: Usuario = Depends(usuario_atual)):
    return {"id": usuario.id, "email": usuario.email}


@app.get("/api/auth/exportar")
def exportar_dados(
    usuario: Usuario = Depends(usuario_atual), db: Session = Depends(sessao)
):
    perfil = _perfil_do_usuario(db, usuario)
    vagas = list(db.scalars(select(VagaDB).where(VagaDB.usuario_id == usuario.id)).all())
    return {
        "usuario": {"id": usuario.id, "email": usuario.email, "criado_em": usuario.criado_em},
        "perfil": perfil.dados,
        "cv_base": perfil.cv_base,
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
    response: Response,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(sessao),
):
    if not verificar_senha(payload.senha, usuario.senha_hash):
        raise HTTPException(403, "Senha incorreta.")
    db.delete(usuario)
    db.commit()
    response.delete_cookie(COOKIE_SESSAO, httponly=True, samesite="lax")


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
    registro = _perfil_do_usuario(db, usuario)
    registro.dados = perfil.model_dump()
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
    ativa = db.scalar(select(BuscaDB).where(
        BuscaDB.usuario_id == usuario.id,
        BuscaDB.estado.in_(["pendente", "processando"]),
    ))
    if ativa:
        raise HTTPException(409, "Já existe uma busca em andamento para esta conta.")
    pedido = (payload.pedido or perfil.pedido_padrao()).strip()
    if not pedido:
        raise HTTPException(400, "Informe o tipo de vaga desejado.")
    busca = BuscaDB(usuario_id=usuario.id, pedido=pedido, limite=payload.limite)
    db.add(busca)
    db.commit()
    db.refresh(busca)
    agendar(busca.id)
    return _busca_resposta(busca)


@app.get("/api/buscas/atual", response_model=Optional[BuscaResposta])
def busca_atual(usuario: Usuario = Depends(usuario_atual), db: Session = Depends(sessao)):
    busca = db.scalar(
        select(BuscaDB).where(BuscaDB.usuario_id == usuario.id)
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
