"""Estado de segurança compartilhado por todos os processos da API."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class SessaoAuthDB(Base):
    __tablename__ = "sessoes_auth"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    usuario_id: Mapped[str] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), index=True)
    criada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class RecuperacaoSenhaDB(Base):
    __tablename__ = "recuperacoes_senha"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    usuario_id: Mapped[str] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), index=True)
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class LimiteAuthDB(Base):
    __tablename__ = "limites_auth"

    chave: Mapped[str] = mapped_column(String(64), primary_key=True)
    tentativas: Mapped[int] = mapped_column(Integer, nullable=False)
    expira_em: Mapped[int] = mapped_column(Integer, index=True)


class AceiteTermosDB(Base):
    __tablename__ = "aceites_termos"

    usuario_id: Mapped[str] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), primary_key=True)
    versao: Mapped[str] = mapped_column(String(64))
    termos_url: Mapped[str] = mapped_column(String(2048), default="")
    privacidade_url: Mapped[str] = mapped_column(String(2048), default="")
    aceito_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
