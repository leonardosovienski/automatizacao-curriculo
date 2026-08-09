"""Schema SaaS multiusuário inicial."""

import sqlalchemy as sa
from alembic import op

revision = "0001_saas"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "usuarios",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("senha_hash", sa.String(255), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_usuarios_email", "usuarios", ["email"], unique=True)
    op.create_table(
        "perfis",
        sa.Column("usuario_id", sa.String(36), sa.ForeignKey("usuarios.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("dados", sa.JSON(), nullable=False),
        sa.Column("cv_base", sa.Text(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "vagas",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("usuario_id", sa.String(36), sa.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vaga_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("score_final", sa.Float(), nullable=True),
        sa.Column("analisado_em", sa.String(40), nullable=False),
        sa.Column("texto", sa.Text(), nullable=False),
        sa.Column("analise", sa.JSON(), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.UniqueConstraint("usuario_id", "vaga_id", name="uq_vaga_usuario"),
    )
    op.create_index("ix_vagas_usuario_id", "vagas", ["usuario_id"])
    op.create_index("ix_vagas_status", "vagas", ["status"])


def downgrade():
    op.drop_table("vagas")
    op.drop_table("perfis")
    op.drop_table("usuarios")
