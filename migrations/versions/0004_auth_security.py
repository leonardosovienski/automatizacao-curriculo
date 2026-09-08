"""Sessões revogáveis, recuperação de senha e limitação compartilhada."""

import sqlalchemy as sa
from alembic import op

revision = "0004_auth_security"
down_revision = "0003_assinaturas"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sessoes_auth",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("usuario_id", sa.String(36), sa.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("criada_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sessoes_auth_usuario_id", "sessoes_auth", ["usuario_id"])
    op.create_index("ix_sessoes_auth_expira_em", "sessoes_auth", ["expira_em"])
    op.create_table(
        "recuperacoes_senha",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("usuario_id", sa.String(36), sa.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_recuperacoes_senha_usuario_id", "recuperacoes_senha", ["usuario_id"])
    op.create_index("ix_recuperacoes_senha_expira_em", "recuperacoes_senha", ["expira_em"])
    op.create_table(
        "limites_auth",
        sa.Column("chave", sa.String(64), primary_key=True),
        sa.Column("tentativas", sa.Integer(), nullable=False),
        sa.Column("expira_em", sa.Integer(), nullable=False),
    )
    op.create_index("ix_limites_auth_expira_em", "limites_auth", ["expira_em"])
    op.create_table(
        "aceites_termos",
        sa.Column("usuario_id", sa.String(36), sa.ForeignKey("usuarios.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("versao", sa.String(64), nullable=False),
        sa.Column("termos_url", sa.String(2048), nullable=False),
        sa.Column("privacidade_url", sa.String(2048), nullable=False),
        sa.Column("aceito_em", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("aceites_termos")
    op.drop_table("limites_auth")
    op.drop_table("recuperacoes_senha")
    op.drop_table("sessoes_auth")
