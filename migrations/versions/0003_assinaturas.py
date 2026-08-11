"""Assinatura Stripe por usuário."""

import sqlalchemy as sa
from alembic import op

revision = "0003_assinaturas"
down_revision = "0002_buscas"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "assinaturas",
        sa.Column("usuario_id", sa.String(36), sa.ForeignKey("usuarios.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("stripe_customer_id", sa.String(64), nullable=False),
        sa.Column("stripe_subscription_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("preco_id", sa.String(64), nullable=True),
        sa.Column("periodo_atual_fim", sa.DateTime(timezone=True), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_assinaturas_stripe_customer_id", "assinaturas", ["stripe_customer_id"])
    op.create_index("ix_assinaturas_stripe_subscription_id", "assinaturas", ["stripe_subscription_id"])


def downgrade():
    op.drop_table("assinaturas")
