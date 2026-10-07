"""trechos: disciplina_id (desnormalizado, protegido por FK composta) e pagina_fim

POR QUE COPIAR disciplina_id PARA trechos
Toda busca é "trechos parecidos DESTA disciplina". Sem a coluna, o filtro só
existe depois de um JOIN com materiais, acima do índice vetorial. Com a coluna
em trechos, filtro e índice ficam na mesma tabela, e o planejador ganha opções:
  - HNSW + filtro (bom quando a disciplina tem muitos trechos), ou
  - B-tree em disciplina_id + ordenar por distância = busca EXATA (bom quando a
    disciplina é pequena; muitas vezes mais rápido e com recall de 100%).
É uma desnormalização: disciplina_id é determinado por material_id (dependência
transitiva, fere a 3FN).

COMO O BANCO IMPEDE A CÓPIA DE DIVERGIR: FK COMPOSTA
A FK simples trechos(material_id) -> materiais(id) vira
    trechos(material_id, disciplina_id) -> materiais(id, disciplina_id)
Assim é impossível gravar um trecho dizendo "sou da disciplina 7" se o material
dele é da disciplina 9: o par precisa existir em materiais.
  - Uma FK precisa apontar para colunas com UNIQUE (ou PK). (id, disciplina_id)
    já é único porque id é PK, mas o Postgres exige a constraint declarada:
    criamos uq_materiais_id_disciplina_id (custa um índice a mais em materiais).
  - ON UPDATE CASCADE: se um material mudar de disciplina, o Postgres atualiza a
    cópia em todos os trechos dele automaticamente.

ADICIONAR COLUNA NOT NULL NUMA TABELA QUE JÁ TEM LINHAS (3 passos)
  1. ADD COLUMN nullable (não dá para exigir NOT NULL de linhas sem valor),
  2. UPDATE ... FROM preenchendo a partir de materiais,
  3. SET NOT NULL (o Postgres varre a tabela para conferir).

pagina_fim: trechos podem atravessar páginas; pagina = onde começa.

Revision ID: 0652a19e3fc0
Revises: 0487cfbe0ee2
Create Date: 2026-10-07 01:47:12.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0652a19e3fc0"
down_revision: Union[str, Sequence[str], None] = "0487cfbe0ee2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        op.f("uq_materiais_id_disciplina_id"), "materiais", ["id", "disciplina_id"]
    )

    op.add_column("trechos", sa.Column("disciplina_id", sa.BigInteger(), nullable=True))
    op.execute(
        """
        UPDATE trechos AS t
        SET disciplina_id = m.disciplina_id
        FROM materiais AS m
        WHERE m.id = t.material_id
        """
    )
    op.alter_column("trechos", "disciplina_id", nullable=False)

    op.drop_constraint(op.f("fk_trechos_material_id_materiais"), "trechos", type_="foreignkey")
    op.create_foreign_key(
        op.f("fk_trechos_material_id_materiais"),
        "trechos",
        "materiais",
        ["material_id", "disciplina_id"],
        ["id", "disciplina_id"],
        ondelete="CASCADE",
        onupdate="CASCADE",
    )
    # Para "trechos da disciplina X" (busca exata filtrada, contagens).
    op.create_index("ix_trechos_disciplina_id", "trechos", ["disciplina_id"])

    op.add_column("trechos", sa.Column("pagina_fim", sa.Integer(), nullable=True))
    op.create_check_constraint(
        op.f("ck_trechos_pagina_fim_valida"), "trechos", "pagina_fim >= pagina"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_trechos_pagina_fim_valida"), "trechos", type_="check")
    op.drop_column("trechos", "pagina_fim")
    op.drop_index("ix_trechos_disciplina_id", table_name="trechos")
    op.drop_constraint(op.f("fk_trechos_material_id_materiais"), "trechos", type_="foreignkey")
    op.create_foreign_key(
        op.f("fk_trechos_material_id_materiais"),
        "trechos",
        "materiais",
        ["material_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_column("trechos", "disciplina_id")
    op.drop_constraint(op.f("uq_materiais_id_disciplina_id"), "materiais", type_="unique")
