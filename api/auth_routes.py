"""Cadastro, login e recuperação de conta com respostas sem exposição de tokens."""

import hashlib
import logging
import os
import secrets
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from jwt import InvalidTokenError
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from triagem.perfil_usuario import PerfilUsuario

from .auth import (
    COOKIE_SESSAO,
    criar_token,
    decodificar_token,
    hash_senha,
    revogar_sessoes,
    verificar_senha,
)
from .auth_models import AceiteTermosDB, RecuperacaoSenhaDB, SessaoAuthDB
from .database import PerfilDB, Usuario, sessao
from .security import limitar_auth

router = APIRouter(prefix="/api/auth", tags=["autenticação"])
logger = logging.getLogger(__name__)
_HASH_FICTICIO = hash_senha(secrets.token_urlsafe(32))
_MENSAGEM_RECUPERACAO = "Se este e-mail estiver cadastrado, você receberá um link para redefinir a senha."


class CadastroPayload(BaseModel):
    email: EmailStr
    senha: str = Field(min_length=10, max_length=128)
    aceite_termos: bool = False


class LoginPayload(BaseModel):
    email: EmailStr
    senha: str = Field(min_length=1, max_length=128)


class RecuperarSenhaPayload(BaseModel):
    email: EmailStr


class RedefinirSenhaPayload(BaseModel):
    token: str = Field(min_length=32, max_length=256)
    senha: str = Field(min_length=10, max_length=128)


class SessaoResposta(BaseModel):
    usuario: dict


def cookie_seguro() -> bool:
    return (
        os.environ.get("TRIAGEM_ENV") == "production"
        or os.environ.get("TRIAGEM_PUBLIC_URL", "").startswith("https://")
    )


def gravar_cookie(response: Response, usuario: Usuario, db: Session) -> None:
    response.set_cookie(
        COOKIE_SESSAO, criar_token(usuario, db), httponly=True,
        secure=cookie_seguro(), samesite="lax", path="/",
        max_age=int(os.environ.get("TRIAGEM_TOKEN_MINUTES", "60")) * 60,
    )


@router.post("/cadastro", response_model=SessaoResposta, status_code=201)
def cadastro(payload: CadastroPayload, request: Request, response: Response, db: Session = Depends(sessao)):
    email = str(payload.email).lower()
    limitar_auth(request, db, "cadastro", email)
    if os.environ.get("TRIAGEM_ENV") == "production" and not payload.aceite_termos:
        raise HTTPException(422, "É necessário aceitar os termos de uso e a política de privacidade.")
    usuario = Usuario(email=email, senha_hash=hash_senha(payload.senha))
    db.add(usuario)
    try:
        db.flush()
        perfil = PerfilUsuario(nome=usuario.email.split("@", 1)[0])
        db.add(PerfilDB(usuario_id=usuario.id, dados=perfil.model_dump(), cv_base=""))
        if payload.aceite_termos:
            db.add(AceiteTermosDB(
                usuario_id=usuario.id,
                versao=os.environ.get("TRIAGEM_TERMS_VERSION", "2026-09-08"),
                termos_url=os.environ.get("TRIAGEM_TERMS_URL", ""),
                privacidade_url=os.environ.get("TRIAGEM_PRIVACY_URL", ""),
            ))
        gravar_cookie(response, usuario, db)
    except IntegrityError as erro:
        db.rollback()
        raise HTTPException(409, "Já existe uma conta com este e-mail.") from erro
    return SessaoResposta(usuario={"id": usuario.id, "email": usuario.email})


@router.post("/login", response_model=SessaoResposta)
def login(payload: LoginPayload, request: Request, response: Response, db: Session = Depends(sessao)):
    email = str(payload.email).lower()
    limitar_auth(request, db, "login", email)
    # Serializa login com redefinição: a senha antiga não pode abrir uma sessão
    # enquanto a redefinição está revogando as sessões anteriores.
    usuario = db.scalar(select(Usuario).where(Usuario.email == email).with_for_update())
    senha_correta = verificar_senha(payload.senha, usuario.senha_hash if usuario else _HASH_FICTICIO)
    if not usuario or not usuario.ativo or not senha_correta:
        raise HTTPException(401, "E-mail ou senha inválidos.")
    db.execute(delete(SessaoAuthDB).where(SessaoAuthDB.expira_em <= datetime.now(timezone.utc)))
    gravar_cookie(response, usuario, db)
    return SessaoResposta(usuario={"id": usuario.id, "email": usuario.email})


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(sessao)):
    autorizacao = request.headers.get("authorization", "")
    token = autorizacao[7:] if autorizacao.lower().startswith("bearer ") else request.cookies.get(COOKIE_SESSAO)
    if token:
        try:
            payload = decodificar_token(token)
            db.execute(delete(SessaoAuthDB).where(
                SessaoAuthDB.id == payload["jti"], SessaoAuthDB.usuario_id == payload["sub"],
            ))
            db.commit()
        except InvalidTokenError:
            pass
    response.delete_cookie(COOKIE_SESSAO, httponly=True, secure=cookie_seguro(), samesite="lax", path="/")


def _configuracao_email() -> tuple[str, str]:
    base = os.environ.get("TRIAGEM_PUBLIC_URL", "").rstrip("/")
    url = urlsplit(base)
    if (
        not os.environ.get("TRIAGEM_SMTP_HOST") or not os.environ.get("TRIAGEM_SMTP_FROM")
        or url.scheme not in {"https", "http"} or not url.netloc or url.username
        or url.query or url.fragment
    ):
        raise HTTPException(503, "A recuperação por e-mail ainda não está configurada.")
    seguranca = os.environ.get("TRIAGEM_SMTP_SECURITY", "starttls")
    if seguranca not in {"starttls", "ssl", "plain"} or (
        os.environ.get("TRIAGEM_ENV") == "production" and (seguranca == "plain" or url.scheme != "https")
    ):
        raise HTTPException(503, "A recuperação por e-mail ainda não está configurada.")
    return base, seguranca


def enviar_recuperacao(email: str, link: str, seguranca: str) -> None:
    mensagem = EmailMessage()
    mensagem["Subject"] = "Redefina sua senha — Triagem de Vagas"
    mensagem["From"] = os.environ["TRIAGEM_SMTP_FROM"]
    mensagem["To"] = email
    mensagem.set_content(
        "Recebemos um pedido para redefinir a senha da sua conta.\n\n"
        f"Acesse o link em até 30 minutos:\n{link}\n\n"
        "Se você não fez este pedido, ignore esta mensagem. Sua senha continua a mesma.\n"
    )
    host = os.environ["TRIAGEM_SMTP_HOST"]
    porta = int(os.environ.get("TRIAGEM_SMTP_PORT", "465" if seguranca == "ssl" else "587"))
    try:
        contexto = ssl.create_default_context()
        smtp = smtplib.SMTP_SSL(host, porta, timeout=10, context=contexto) if seguranca == "ssl" else smtplib.SMTP(host, porta, timeout=10)
        with smtp:
            if seguranca == "starttls":
                smtp.starttls(context=contexto)
            if os.environ.get("TRIAGEM_SMTP_USERNAME"):
                smtp.login(os.environ["TRIAGEM_SMTP_USERNAME"], os.environ.get("TRIAGEM_SMTP_PASSWORD", ""))
            smtp.send_message(mensagem)
    except (OSError, smtplib.SMTPException) as erro:
        # Não registrar endereço, link ou token; o operador monitora esta mensagem.
        logger.error("Falha no envio de recuperação de senha (%s).", type(erro).__name__)


@router.post("/recuperar-senha", status_code=202)
def recuperar_senha(
    payload: RecuperarSenhaPayload, request: Request, background: BackgroundTasks,
    db: Session = Depends(sessao),
):
    email = str(payload.email).lower()
    limitar_auth(request, db, "recuperar", email)
    base, seguranca = _configuracao_email()
    usuario = db.scalar(select(Usuario).where(Usuario.email == email, Usuario.ativo.is_(True)).with_for_update())
    if usuario:
        token = secrets.token_urlsafe(32)
        db.execute(delete(RecuperacaoSenhaDB).where(RecuperacaoSenhaDB.usuario_id == usuario.id))
        db.add(RecuperacaoSenhaDB(
            token_hash=hashlib.sha256(token.encode()).hexdigest(), usuario_id=usuario.id,
            expira_em=datetime.now(timezone.utc) + timedelta(minutes=30),
        ))
        db.commit()
        background.add_task(enviar_recuperacao, email, f"{base}/redefinir-senha#token={token}", seguranca)
    return {"mensagem": _MENSAGEM_RECUPERACAO}


@router.post("/redefinir-senha")
def redefinir_senha(payload: RedefinirSenhaPayload, request: Request, db: Session = Depends(sessao)):
    limitar_auth(request, db, "redefinir")
    digest = hashlib.sha256(payload.token.encode()).hexdigest()
    registro = db.get(RecuperacaoSenhaDB, digest)
    # Mantém a mesma ordem de locks da emissão do link: usuário, depois token.
    usuario = db.scalar(select(Usuario).where(Usuario.id == registro.usuario_id).with_for_update()) if registro else None
    # DELETE RETURNING torna o consumo de um token atômico entre workers.
    usuario_id = db.execute(delete(RecuperacaoSenhaDB).where(
        RecuperacaoSenhaDB.token_hash == digest,
        RecuperacaoSenhaDB.expira_em > datetime.now(timezone.utc),
    ).returning(RecuperacaoSenhaDB.usuario_id).execution_options(synchronize_session="fetch")).scalar_one_or_none()
    if not usuario_id or not usuario or not usuario.ativo:
        db.rollback()
        raise HTTPException(400, "Link inválido ou expirado. Solicite uma nova recuperação.")
    usuario.senha_hash = hash_senha(payload.senha)
    revogar_sessoes(db, usuario.id)
    db.execute(delete(RecuperacaoSenhaDB).where(RecuperacaoSenhaDB.usuario_id == usuario.id))
    db.commit()
    return {"mensagem": "Senha redefinida. Entre novamente com a nova senha."}
