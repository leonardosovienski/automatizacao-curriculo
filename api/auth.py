"""Senhas Argon2 e sessões revogáveis validadas no banco a cada requisição."""

import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from sqlalchemy import delete
from sqlalchemy.orm import Session

from .auth_models import SessaoAuthDB
from .database import Usuario, sessao

password_hash = PasswordHash.recommended()
bearer = HTTPBearer(auto_error=False)
COOKIE_SESSAO = "triagem_session"
ALGORITMO = "HS256"
EMISSOR = "triagem-vagas"
AUDIENCIA = "triagem-api"


def _segredo() -> str:
    segredo = os.environ.get("TRIAGEM_JWT_SECRET")
    if segredo:
        return segredo
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return "segredo-de-teste-nao-usar-em-producao"
    raise RuntimeError("TRIAGEM_JWT_SECRET é obrigatória.")


def hash_senha(senha: str) -> str:
    return password_hash.hash(senha)


def verificar_senha(senha: str, hash_atual: str) -> bool:
    try:
        return password_hash.verify(senha, hash_atual)
    except (UnknownHashError, ValueError):
        return False


def criar_token(usuario: Usuario, db: Session) -> str:
    agora = datetime.now(timezone.utc)
    expiracao = agora + timedelta(minutes=int(os.environ.get("TRIAGEM_TOKEN_MINUTES", "60")))
    registro = SessaoAuthDB(usuario_id=usuario.id, expira_em=expiracao)
    db.add(registro)
    db.flush()
    token = jwt.encode(
        {"sub": usuario.id, "jti": registro.id, "iat": agora, "exp": expiracao,
         "iss": EMISSOR, "aud": AUDIENCIA},
        _segredo(), algorithm=ALGORITMO,
    )
    db.commit()
    return token


def decodificar_token(token: str) -> dict:
    payload = jwt.decode(
        token, _segredo(), algorithms=[ALGORITMO], issuer=EMISSOR, audience=AUDIENCIA,
        options={"require": ["sub", "jti", "iat", "exp", "iss", "aud"]},
    )
    if not isinstance(payload["sub"], str) or not isinstance(payload["jti"], str):
        raise InvalidTokenError("Identificador inválido.")
    return payload


def revogar_sessoes(db: Session, usuario_id: str) -> None:
    db.execute(delete(SessaoAuthDB).where(SessaoAuthDB.usuario_id == usuario_id))


def usuario_atual(
    request: Request,
    credencial: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(sessao),
) -> Usuario:
    token = credencial.credentials if credencial else request.cookies.get(COOKIE_SESSAO)
    if not token:
        raise HTTPException(401, "Autenticação necessária.", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = decodificar_token(token)
        usuario_id = payload["sub"]
    except InvalidTokenError as e:
        raise HTTPException(401, "Sessão inválida ou expirada.") from e
    registro = db.get(SessaoAuthDB, payload["jti"])
    expiracao = registro.expira_em if registro else None
    if expiracao and expiracao.tzinfo is None:
        expiracao = expiracao.replace(tzinfo=timezone.utc)
    if (
        not registro or registro.usuario_id != usuario_id
        or expiracao <= datetime.now(timezone.utc)
    ):
        raise HTTPException(401, "Sessão inválida ou expirada.")
    usuario = db.get(Usuario, usuario_id)
    if not usuario or not usuario.ativo:
        raise HTTPException(401, "Usuário inválido ou inativo.")
    return usuario
