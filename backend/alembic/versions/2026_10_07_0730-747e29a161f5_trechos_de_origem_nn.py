"""trechos de origem N:N: flashcard_trechos e questao_trechos

ANTES (fase 1): flashcards.trecho_id e questoes.trecho_id, uma FK simples.
Isso modela uma relação 1:N: cada card aponta para NO MÁXIMO UM trecho.

O PROBLEMA: um flashcard gerado pelo LLM costuma juntar informação de 2 ou 3
trechos ("o que é atomicidade" pode vir do trecho que define e do que dá o
exemplo). E um trecho gera vários cards. Isso é uma relação N:N (muitos para
muitos), e o modelo relacional representa N:N com uma TABELA ASSOCIATIVA:

    flashcards 1 ── N flashcard_trechos N ── 1 trechos

- PK composta (flashcard_id, trecho_id): o mesmo par não se repete, e o índice
  da PK atende "trechos de um card" (prefixo flashcard_id).
- Índice separado em trecho_id: atende "cards que vieram deste trecho" e a FK
  (apagar um trecho precisa achar as associações dele).
- ON DELETE CASCADE nas duas FKs, mas o efeito é diferente de antes: apagar um
  trecho apaga só a LINHA DE ASSOCIAÇÃO; o flashcard continua existindo, como
  acontecia com o SET NULL da coluna antiga.

Migração dos dados: INSERT ... SELECT copia cada trecho_id existente para a
tabela nova ANTES de remover a coluna. O downgrade é com perda: N:N não cabe
numa coluna, então ele guarda só o menor trecho_id de cada card.

Revision ID: 747e29a161f5
Revises: fcc64ac58779
Create Date: 2026-10-07 07:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "747e29a161f5"
down_revision: Union[str, Sequence[str], None] = "fcc64ac58779"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (tabela dona, tabela associativa, coluna da dona na associativa)
PARES = (("flashcards", "flashcard_trechos", "flashcard_id"), ("questoes", "questao_trechos", "questao_id"))


def upgrade() -> None:
    for dona, assoc, coluna in PARES:
        op.create_table(
            assoc,
            sa.Column(coluna, sa.BigInteger(), nullable=False),
            sa.Column("trecho_id", sa.BigInteger(), nullable=False),
            sa.PrimaryKeyConstraint(coluna, "trecho_id", name=op.f(f"pk_{assoc}")),
            sa.ForeignKeyConstraint(
                [coluna], [f"{dona}.id"], name=op.f(f"fk_{assoc}_{coluna}_{dona}"), ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["trecho_id"], ["trechos.id"], name=op.f(f"fk_{assoc}_trecho_id_trechos"),
                ondelete="CASCADE",
            ),
        )
        op.create_index(f"ix_{assoc}_trecho_id", assoc, ["trecho_id"])
        op.execute(
            f"INSERT INTO {assoc} ({coluna}, trecho_id) "
            f"SELECT id, trecho_id FROM {dona} WHERE trecho_id IS NOT NULL"
        )
        # DROP COLUMN leva junto a FK da coluna; o índice parcial apagamos antes.
        op.drop_index(f"ix_{dona}_trecho_id", table_name=dona)
        op.drop_column(dona, "trecho_id")


def downgrade() -> None:
    for dona, assoc, coluna in PARES:
        op.add_column(dona, sa.Column("trecho_id", sa.BigInteger(), nullable=True))
        op.execute(
            f"UPDATE {dona} AS d SET trecho_id = a.trecho_id "
            f"FROM (SELECT {coluna}, min(trecho_id) AS trecho_id FROM {assoc} GROUP BY {coluna}) AS a "
            f"WHERE a.{coluna} = d.id"
        )
        op.create_foreign_key(
            op.f(f"fk_{dona}_trecho_id_trechos"), dona, "trechos", ["trecho_id"], ["id"],
            ondelete="SET NULL",
        )
        op.create_index(
            f"ix_{dona}_trecho_id", dona, ["trecho_id"], postgresql_where=sa.text("trecho_id IS NOT NULL")
        )
        op.drop_table(assoc)
