"""flashcards.embedding para deduplicação por similaridade

Antes de salvar um flashcard gerado, comparamos o embedding da frente dele com
os flashcards existentes da mesma disciplina (operador <=>) e descartamos os
muito parecidos. Limiar e calibração em docs/geracao-llm.md.

SEM índice HNSW, de propósito: a comparação é sempre "dentro de UMA
disciplina", que tem dezenas a centenas de cards. O experimento da fase 2
mostrou que, num subconjunto pequeno, filtrar por B-tree e calcular a
distância exata (recall 100%) é mais rápido que o HNSW. O índice existente
ix_flashcards_disciplina_proxima_revisao começa por disciplina_id e serve.
Cards criados à mão continuam com embedding nulo (não entram na comparação).

Revision ID: fbf3e5b1d856
Revises: 527ce7983102
Create Date: 2026-10-07 07:32:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "fbf3e5b1d856"
down_revision: Union[str, Sequence[str], None] = "527ce7983102"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("flashcards", sa.Column("embedding", Vector(384), nullable=True))


def downgrade() -> None:
    op.drop_column("flashcards", "embedding")
