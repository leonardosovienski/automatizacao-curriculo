"""Reserva de buscas e exclusão mútua de trabalho por usuário."""

import sqlalchemy as sa
from alembic import op

revision = "0006_durable_jobs"
down_revision = "0005_billing"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("buscas", sa.Column("iniciada_em", sa.DateTime(timezone=True)))
    op.add_column("buscas", sa.Column("expira_em", sa.DateTime(timezone=True)))
    op.add_column("buscas", sa.Column("worker_token", sa.String(36)))
    op.add_column("buscas", sa.Column("tipo", sa.String(16), nullable=False, server_default="busca"))
    op.add_column("buscas", sa.Column("vaga_alvo_id", sa.String(64)))
    op.add_column("buscas", sa.Column("resultado", sa.Text()))
    # O worker antigo não deixava lease nem checkpoints recuperáveis.
    op.execute("UPDATE buscas SET estado='falhou', progresso=100, "
               "mensagem='Busca interrompida pela atualização. Inicie uma nova busca.' "
               "WHERE estado IN ('pendente', 'processando')")
    op.create_index("uq_busca_ativa_usuario", "buscas", ["usuario_id"], unique=True,
                    sqlite_where=sa.text("estado IN ('pendente', 'processando')"),
                    postgresql_where=sa.text("estado IN ('pendente', 'processando')"))


def downgrade():
    op.drop_index("uq_busca_ativa_usuario", table_name="buscas")
    for nome in ("resultado", "vaga_alvo_id", "tipo", "worker_token", "expira_em", "iniciada_em"):
        op.drop_column("buscas", nome)
