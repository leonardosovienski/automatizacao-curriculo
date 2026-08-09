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
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def _url_banco() -> str:
    configurada = os.environ.get("DATABASE_URL")
    if configurada:
        return configurada.replace("postgres://", "postgresql+psycopg://", 1)
    caminho = Path(os.environ.get("TRIAGEM_DATABASE") or Path.cwd() / "triagem.db")
    return f"sqlite:///{caminho.as_posix()}"


DATABASE_URL = _url_banco()
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


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


def criar_tabelas() -> None:
    Base.metadata.create_all(engine)


def sessao():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
