"""Autenticação por senha Argon2 e access token JWT curto."""

import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from .database import Usuario, sessao

password_hash = PasswordHash.recommended()
bearer = HTTPBearer(auto_error=False)
COOKIE_SESSAO = "triagem_session"
ALGORITMO = "HS256"
EXPIRACAO_MINUTOS = int(os.environ.get("TRIAGEM_TOKEN_MINUTES", "60"))


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
    return password_hash.verify(senha, hash_atual)


def criar_token(usuario: Usuario) -> str:
    agora = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": usuario.id, "iat": agora, "exp": agora + timedelta(minutes=EXPIRACAO_MINUTOS)},
        _segredo(), algorithm=ALGORITMO,
    )


def usuario_atual(
    request: Request,
    credencial: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(sessao),
) -> Usuario:
    token = credencial.credentials if credencial else request.cookies.get(COOKIE_SESSAO)
    if not token:
        raise HTTPException(401, "Autenticação necessária.", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(token, _segredo(), algorithms=[ALGORITMO])
        usuario_id = payload.get("sub")
    except InvalidTokenError as e:
        raise HTTPException(401, "Sessão inválida ou expirada.") from e
    usuario = db.get(Usuario, usuario_id)
    if not usuario or not usuario.ativo:
        raise HTTPException(401, "Usuário inválido ou inativo.")
    return usuario
