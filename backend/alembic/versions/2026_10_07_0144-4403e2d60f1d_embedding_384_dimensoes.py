"""embedding com 384 dimensões

A fase 1 criou trechos.embedding como vector(1024), pensando em voyage-3.5 ou
bge-m3. Na fase 2 escolhemos um modelo local e leve, intfloat/multilingual-e5-small,
que gera vetores de 384 dimensões. Um vector(n) só aceita vetores com exatamente
n números, então a coluna precisa mudar.

Por que uma migration NOVA e não editar a inicial: a inicial já foi aplicada (no
banco de dev, no de teste, e estaria em produção). O Alembic registra só "estou na
revisão X" em alembic_version; editar X não reaplica nada nos bancos que já a
rodaram, e os schemas divergiriam silenciosamente.

USING NULL: não existe conversão de um vetor de 1024 dimensões para 384. Cada
modelo define o seu próprio espaço vetorial; truncar ou projetar os números não
produz um embedding válido do modelo novo. Trocar de modelo = descartar os
embeddings e recalcular todos (aqui a tabela ainda está vazia).

O índice HNSW é apagado antes e recriado depois: ele foi construído para
vector(1024) e não sobrevive à troca de tipo.

Revision ID: 4403e2d60f1d
Revises: 6703a2c50931
Create Date: 2026-10-07 01:44:12.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "4403e2d60f1d"
down_revision: Union[str, Sequence[str], None] = "6703a2c50931"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _trocar_dimensao(dim: int) -> None:
    op.drop_index("ix_trechos_embedding_hnsw", table_name="trechos")
    op.execute(f"ALTER TABLE trechos ALTER COLUMN embedding TYPE vector({dim}) USING NULL")
    op.create_index(
        "ix_trechos_embedding_hnsw",
        "trechos",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def upgrade() -> None:
    _trocar_dimensao(384)


def downgrade() -> None:
    _trocar_dimensao(1024)
