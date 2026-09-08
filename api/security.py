"""Proteções HTTP e limite de tentativas atômico no banco compartilhado."""

import hashlib
import hmac
import os
import secrets
import time
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from sqlalchemy import case, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session
from starlette.datastructures import URL, Headers
from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse

from .auth import COOKIE_SESSAO, _segredo
from .auth_models import LimiteAuthDB


def limitar_tentativas(db: Session, chave: str, *, limite: int, janela: int = 900) -> None:
    """Incrementa uma janela fixa sem corrida entre workers (PostgreSQL/SQLite)."""
    agora = int(time.time())
    digest = hmac.new(_segredo().encode(), chave.encode(), hashlib.sha256).hexdigest()
    inserir = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
    expirado = LimiteAuthDB.expira_em <= agora
    comando = inserir(LimiteAuthDB).values(chave=digest, tentativas=1, expira_em=agora + janela)
    comando = comando.on_conflict_do_update(
        index_elements=[LimiteAuthDB.chave],
        set_={
            "tentativas": case(
                (expirado, 1), (LimiteAuthDB.tentativas <= limite, LimiteAuthDB.tentativas + 1),
                else_=LimiteAuthDB.tentativas,
            ),
            "expira_em": case((expirado, agora + janela), else_=LimiteAuthDB.expira_em),
        },
    ).returning(LimiteAuthDB.tentativas, LimiteAuthDB.expira_em)
    tentativas, expira_em = db.execute(comando).one()
    if secrets.randbelow(64) == 0:
        db.execute(delete(LimiteAuthDB).where(LimiteAuthDB.expira_em < agora - 3600))
    db.commit()
    if tentativas > limite:
        raise HTTPException(
            429, "Muitas tentativas. Aguarde alguns minutos e tente novamente.",
            headers={"Retry-After": str(max(1, expira_em - agora))},
        )


def limitar_auth(request: Request, db: Session, acao: str, email: str | None = None) -> None:
    # request.client é validado pelo proxy de confiança do ASGI; jamais confiar em
    # X-Forwarded-For diretamente, pois um cliente pode enviá-lo arbitrariamente.
    ip = request.client.host if request.client else "desconhecido"
    limite_ip = {"login": 30, "cadastro": 10, "recuperar": 10, "redefinir": 20}.get(acao, 20)
    limitar_tentativas(db, f"auth:{acao}:ip:{ip}", limite=limite_ip)
    if email:
        limitar_tentativas(db, f"auth:{acao}:conta:{email.strip().lower()}", limite=8)


def _origem(valor: str) -> str:
    try:
        partes = urlsplit(valor)
        if partes.scheme not in {"http", "https"} or not partes.hostname or partes.username:
            return ""
        porta = partes.port
        host = partes.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        if porta and porta != {"http": 80, "https": 443}[partes.scheme]:
            host += f":{porta}"
        return f"{partes.scheme}://{host}"
    except ValueError:
        return ""


class SecurityMiddleware:
    """Rejeita CSRF e corpos grandes antes de Pydantic/JSON carregarem o corpo."""

    def __init__(self, app, origens: list[str], max_body_bytes: int = 1_048_576):
        self.app = app
        self.origens = {_origem(origem) for origem in origens} - {""}
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        caminho = scope.get("path", "")

        async def send_seguro(message):
            if message["type"] == "http.response.start":
                novos = list(message.get("headers", []))
                existentes = {chave.lower() for chave, _ in novos}
                for chave, valor in (
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"cache-control", b"no-store"),
                ):
                    if chave not in existentes:
                        novos.append((chave, valor))
                if caminho.startswith(("/api/", "/billing/")):
                    novos.append((b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"))
                if os.environ.get("TRIAGEM_ENV") == "production":
                    novos.append((b"strict-transport-security", b"max-age=31536000"))
                message = {**message, "headers": novos}
            await send(message)

        async def rejeitar(status, detalhe):
            await JSONResponse({"detail": detalhe}, status_code=status)(scope, receive, send_seguro)

        mutacao = scope["method"] not in {"GET", "HEAD", "OPTIONS"}
        # O webhook usa assinatura Stripe e não uma sessão de navegador.
        if mutacao and caminho != "/billing/webhook":
            origem_bruta = headers.get("origin") or headers.get("referer")
            permitidas = self.origens | {_origem(str(URL(scope=scope)))}
            if origem_bruta and _origem(origem_bruta) not in permitidas:
                await rejeitar(403, "Origem da requisição não autorizada.")
                return
            cookies = StarletteRequest(scope).cookies
            if not origem_bruta and (
                (COOKIE_SESSAO in cookies and not headers.get("authorization", "").lower().startswith("bearer "))
                or headers.get("sec-fetch-site") in {"cross-site", "same-site"}
            ):
                await rejeitar(403, "Origem necessária para esta operação autenticada.")
                return

        if mutacao:
            tamanho = headers.get("content-length")
            if tamanho:
                try:
                    if int(tamanho) < 0:
                        raise ValueError
                    if int(tamanho) > self.max_body_bytes:
                        await rejeitar(413, "O conteúdo excede o limite permitido.")
                        return
                except ValueError:
                    await rejeitar(400, "Content-Length inválido.")
                    return
            partes = []
            total = 0
            while True:
                mensagem = await receive()
                if mensagem["type"] == "http.disconnect":
                    return
                total += len(mensagem.get("body", b""))
                if total > self.max_body_bytes:
                    await rejeitar(413, "O conteúdo excede o limite permitido.")
                    return
                partes.append(mensagem.get("body", b""))
                if not mensagem.get("more_body", False):
                    break
            corpo = b"".join(partes)
            recebido = False

            async def receber_limitado():
                nonlocal recebido
                if not recebido:
                    recebido = True
                    return {"type": "http.request", "body": corpo, "more_body": False}
                return await receive()

            await self.app(scope, receber_limitado, send_seguro)
        else:
            await self.app(scope, receive, send_seguro)
