"""Deduplicação de webhooks, checkout e cotas mensais."""

import sqlalchemy as sa
from alembic import op

revision = "0005_billing"
down_revision = "0004_auth_security"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("uq_assinaturas_customer", "assinaturas", ["stripe_customer_id"], unique=True)
    op.create_index(
        "uq_assinaturas_subscription", "assinaturas", ["stripe_subscription_id"], unique=True
    )
    op.create_table(
        "billing_controles",
        sa.Column(
            "usuario_id",
            sa.String(36),
            sa.ForeignKey("usuarios.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("checkout_session_id", sa.String(128), nullable=True),
    )
    op.create_table(
        "eventos_stripe",
        sa.Column("evento_id", sa.String(128), primary_key=True),
        sa.Column("tipo", sa.String(96), nullable=False),
        sa.Column("recebido_em", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "uso_mensal",
        sa.Column(
            "usuario_id",
            sa.String(36),
            sa.ForeignKey("usuarios.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("mes", sa.String(7), primary_key=True),
        sa.Column("buscas", sa.Integer(), nullable=False),
        sa.Column("analises", sa.Integer(), nullable=False),
        sa.CheckConstraint("buscas >= 0 AND analises >= 0", name="ck_uso_nao_negativo"),
    )


def downgrade():
    op.drop_table("uso_mensal")
    op.drop_table("eventos_stripe")
    op.drop_table("billing_controles")
    op.drop_index("uq_assinaturas_subscription", table_name="assinaturas")
    op.drop_index("uq_assinaturas_customer", table_name="assinaturas")
