"""limites_taxa: contadores de rate limiting (tabela UNLOGGED)

Uma linha por (chave, janela). A chave diz O QUÊ está sendo limitado
("login:ip:1.2.3.4", "login:email:x@y", "ia:usuario:5") e janela_inicio é o
começo da janela de tempo fixa (date_bin). Cada requisição faz:

    INSERT ... VALUES (:chave, <início da janela>, 1)
    ON CONFLICT (chave, janela_inicio) DO UPDATE SET contagem = contagem + 1
    RETURNING contagem

UPSERT atômico: duas requisições simultâneas não leem o mesmo valor e gravam
"+1" por cima uma da outra (lost update). O ON CONFLICT trava a linha, a 2a espera
e soma em cima do valor novo. Funciona com várias instâncias da API, sem Redis.

UNLOGGED: a tabela não escreve no WAL (o log que garante durabilidade e
replicação). É mais rápida de escrever e, numa queda do servidor, é ESVAZIADA.
Para contadores de janela de minutos isso não importa: no pior caso, o limite
zera. Nunca use UNLOGGED para dado que não pode sumir.

Revision ID: 9a4980aa8776
Revises: 8de4a1970b31
Create Date: 2026-10-08 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9a4980aa8776"
down_revision: Union[str, Sequence[str], None] = "8de4a1970b31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "limites_taxa",
        sa.Column("chave", sa.Text(), nullable=False),
        sa.Column("janela_inicio", sa.DateTime(timezone=True), nullable=False),
        sa.Column("contagem", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("chave", "janela_inicio", name=op.f("pk_limites_taxa")),
        sa.CheckConstraint("contagem > 0", name=op.f("ck_limites_taxa_contagem_positiva")),
        prefixes=["UNLOGGED"],
    )
    # Para a limpeza das janelas vencidas (DELETE ... WHERE janela_inicio < ...).
    op.create_index("ix_limites_taxa_janela_inicio", "limites_taxa", ["janela_inicio"])


def downgrade() -> None:
    op.drop_table("limites_taxa")
