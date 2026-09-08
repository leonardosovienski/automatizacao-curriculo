"""Estado de cobrança e consumo, sem dados de cartão."""

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class BillingControleDB(Base):
    __tablename__ = "billing_controles"
    usuario_id: Mapped[str] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), primary_key=True
    )
    checkout_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


class EventoStripeDB(Base):
    __tablename__ = "eventos_stripe"
    evento_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tipo: Mapped[str] = mapped_column(String(96))
    recebido_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class UsoMensalDB(Base):
    __tablename__ = "uso_mensal"
    __table_args__ = (CheckConstraint("buscas >= 0 AND analises >= 0", name="ck_uso_nao_negativo"),)
    usuario_id: Mapped[str] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), primary_key=True
    )
    mes: Mapped[str] = mapped_column(String(7), primary_key=True)
    buscas: Mapped[int] = mapped_column(default=0)
    analises: Mapped[int] = mapped_column(default=0)
