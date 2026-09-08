"""Persistência multiusuário: SQLite em desenvolvimento, PostgreSQL em produção."""

import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def _url_banco() -> str:
    configurada = os.environ.get("DATABASE_URL")
    if configurada:
        for prefixo in ("postgres://", "postgresql://"):
            if configurada.startswith(prefixo):
                return "postgresql+psycopg://" + configurada[len(prefixo):]
        return configurada
    caminho = Path(os.environ.get("TRIAGEM_DATABASE") or Path.cwd() / "triagem.db")
    return f"sqlite:///{caminho.as_posix()}"


DATABASE_URL = _url_banco()
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    pool_pre_ping=True,
    hide_parameters=True,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@event.listens_for(engine, "connect")
def _integridade_sqlite(connection, _record):
    if DATABASE_URL.startswith("sqlite"):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")


class Base(DeclarativeBase):
    pass


class Usuario(Base):
    __tablename__ = "usuarios"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    senha_hash: Mapped[str] = mapped_column(String(255))
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    perfil: Mapped["PerfilDB | None"] = relationship(back_populates="usuario", cascade="all, delete-orphan")
    vagas: Mapped[list["VagaDB"]] = relationship(back_populates="usuario", cascade="all, delete-orphan")


class PerfilDB(Base):
    __tablename__ = "perfis"

    usuario_id: Mapped[str] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), primary_key=True)
    dados: Mapped[dict] = mapped_column(JSON, default=dict)
    cv_base: Mapped[str] = mapped_column(Text, default="")
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    usuario: Mapped[Usuario] = relationship(back_populates="perfil")


class VagaDB(Base):
    __tablename__ = "vagas"
    __table_args__ = (UniqueConstraint("usuario_id", "vaga_id", name="uq_vaga_usuario"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    usuario_id: Mapped[str] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), index=True)
    vaga_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="novo", index=True)
    score_final: Mapped[float | None] = mapped_column(Float, nullable=True)
    analisado_em: Mapped[str] = mapped_column(String(40), default="")
    texto: Mapped[str] = mapped_column(Text, default="")
    analise: Mapped[dict] = mapped_column(JSON, default=dict)
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    usuario: Mapped[Usuario] = relationship(back_populates="vagas")


class BuscaDB(Base):
    __tablename__ = "buscas"
    __table_args__ = (
        Index("uq_busca_ativa_usuario", "usuario_id", unique=True,
              sqlite_where=text("estado IN ('pendente', 'processando')"),
              postgresql_where=text("estado IN ('pendente', 'processando')")),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    usuario_id: Mapped[str] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), index=True)
    pedido: Mapped[str] = mapped_column(Text)
    limite: Mapped[int] = mapped_column(default=10)
    estado: Mapped[str] = mapped_column(String(24), default="pendente", index=True)
    progresso: Mapped[int] = mapped_column(default=0)
    mensagem: Mapped[str] = mapped_column(Text, default="Aguardando processamento.")
    erro: Mapped[str | None] = mapped_column(Text, nullable=True)
    encontradas: Mapped[int] = mapped_column(default=0)
    criada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    concluida_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    iniciada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expira_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    tipo: Mapped[str] = mapped_column(String(16), default="busca", server_default="busca")
    vaga_alvo_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resultado: Mapped[str | None] = mapped_column(Text, nullable=True)


class AssinaturaDB(Base):
    __tablename__ = "assinaturas"
    __table_args__ = (
        Index("uq_assinaturas_customer", "stripe_customer_id", unique=True),
        Index("uq_assinaturas_subscription", "stripe_subscription_id", unique=True),
    )

    usuario_id: Mapped[str] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), primary_key=True)
    stripe_customer_id: Mapped[str] = mapped_column(String(64), index=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="inativa")
    preco_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    periodo_atual_fim: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    usuario: Mapped[Usuario] = relationship()


def criar_tabelas() -> None:
    from . import auth_models, billing_models  # noqa: F401
    Base.metadata.create_all(engine)


def sessao():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
