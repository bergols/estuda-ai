"""busca textual (full-text search) em trechos

1. Extensão unaccent + configuração de text search 'portugues_unaccent':
   cópia da 'portuguese' em que cada palavra passa primeiro pelo dicionário
   unaccent (tira acentos) e depois pelo stemmer português (reduz ao radical).
   Assim "Índices", "indice" e "índice" viram o mesmo lexema.

2. Coluna GERADA conteudo_tsv:
       GENERATED ALWAYS AS (to_tsvector('portugues_unaccent', conteudo)) STORED
   O Postgres calcula o tsvector em todo INSERT/UPDATE de conteudo; ninguém
   consegue gravar um valor inconsistente. STORED = gravado em disco (o Postgres
   16 só suporta colunas geradas STORED).
   A expressão de uma coluna gerada precisa ser IMMUTABLE (mesma entrada, mesma
   saída, sempre). to_tsvector(texto) sem configuração NÃO é: depende do parâmetro
   default_text_search_config da sessão. Com a configuração explícita
   (::regconfig) a função é IMMUTABLE. Cuidado: se um dia a configuração
   portugues_unaccent for alterada, os tsvectors já gravados não são recalculados
   sozinhos (é preciso um UPDATE trechos SET conteudo = conteudo).

3. Índice GIN (Generalized Inverted Index) em conteudo_tsv: um índice INVERTIDO,
   lexema -> lista de linhas que o contêm, como o índice remissivo de um livro.
   Atende o operador @@ (tsvector casa com tsquery).

Revision ID: fcc64ac58779
Revises: 0652a19e3fc0
Create Date: 2026-10-07 01:48:12.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "fcc64ac58779"
down_revision: Union[str, Sequence[str], None] = "0652a19e3fc0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute("CREATE TEXT SEARCH CONFIGURATION portugues_unaccent (COPY = portuguese)")
    # hword/hword_part/word: tipos de token com letras (inclusive acentuadas).
    op.execute(
        """
        ALTER TEXT SEARCH CONFIGURATION portugues_unaccent
            ALTER MAPPING FOR hword, hword_part, word
            WITH unaccent, portuguese_stem
        """
    )
    op.add_column(
        "trechos",
        sa.Column(
            "conteudo_tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('portugues_unaccent'::regconfig, conteudo)", persisted=True),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_trechos_conteudo_tsv", "trechos", ["conteudo_tsv"], postgresql_using="gin"
    )


def downgrade() -> None:
    op.drop_index("ix_trechos_conteudo_tsv", table_name="trechos")
    op.drop_column("trechos", "conteudo_tsv")
    op.execute("DROP TEXT SEARCH CONFIGURATION portugues_unaccent")
    op.execute("DROP EXTENSION IF EXISTS unaccent")
