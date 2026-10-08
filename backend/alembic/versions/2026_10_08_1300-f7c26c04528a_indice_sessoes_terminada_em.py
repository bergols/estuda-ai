"""índice (usuario_id, terminada_em) INCLUDE (metodo) em sessoes_estudo

Para a análise "taxa de acerto nas revisões feitas logo após cada método de estudo"
(app/servicos/analytics_foco.py). Para CADA resposta, a consulta procura a última
sessão do usuário que terminou até 60 minutos antes:

    LEFT JOIN LATERAL (
        SELECT s.metodo FROM sessoes_estudo s
        WHERE s.usuario_id = r.usuario_id
          AND s.terminada_em <= r.respondido_em
          AND s.terminada_em >  r.respondido_em - 60 min
        ORDER BY s.terminada_em DESC LIMIT 1
    )

Com (usuario_id, terminada_em), cada busca é um Index Scan Backward que para na
primeira linha (top-1 por grupo, como a fila do dia da fase 4). INCLUDE (metodo) põe
o método nas folhas do índice: a busca nem visita a tabela (Index Only Scan, como o
INCLUDE (nota) da fase 5). Parcial (WHERE terminada_em IS NOT NULL): sessões em
andamento nunca entram nessa busca, então não precisam ocupar o índice.

O índice antigo (usuario_id, iniciada_em) continua: serve aos filtros por período
(horas de foco por dia/semana), que olham o INÍCIO da sessão.

Revision ID: f7c26c04528a
Revises: 9364966b7d07
Create Date: 2026-10-08 13:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f7c26c04528a"
down_revision: Union[str, Sequence[str], None] = "9364966b7d07"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_sessoes_estudo_usuario_terminada_em",
        "sessoes_estudo",
        ["usuario_id", "terminada_em"],
        postgresql_include=["metodo"],
        postgresql_where=sa.text("terminada_em IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_sessoes_estudo_usuario_terminada_em", table_name="sessoes_estudo")
