"""Execuções de busca por usuário."""

import sqlalchemy as sa
from alembic import op

revision = "0002_buscas"
down_revision = "0001_saas"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "buscas",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("usuario_id", sa.String(36), sa.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pedido", sa.Text(), nullable=False),
        sa.Column("limite", sa.Integer(), nullable=False),
        sa.Column("estado", sa.String(24), nullable=False),
        sa.Column("progresso", sa.Integer(), nullable=False),
        sa.Column("mensagem", sa.Text(), nullable=False),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.Column("encontradas", sa.Integer(), nullable=False),
        sa.Column("criada_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("concluida_em", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_buscas_usuario_id", "buscas", ["usuario_id"])
    op.create_index("ix_buscas_estado", "buscas", ["estado"])


def downgrade():
    op.drop_table("buscas")
