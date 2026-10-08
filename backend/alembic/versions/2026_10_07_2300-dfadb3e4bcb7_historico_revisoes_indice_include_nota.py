"""historico_revisoes: índice (flashcard_id, revisado_em) INCLUDE (nota)

INCLUDE põe a coluna nota nas folhas do índice sem fazer dela parte da chave
(não entra na ordenação nem na busca). Consultas que só precisam de flashcard_id,
revisado_em e nota (o ramo de revisões de vw_respostas, o ranking de cards
difíceis) passam a ser respondidas só pelo índice: Index Only Scan, sem visitar
a tabela. Desde que o mapa de visibilidade esteja em dia (VACUUM), para o
Postgres saber que a versão no índice é a visível.

Medido em docs/experimentos/analytics.md (200 alunos, 435 mil revisões): no
ranking, Index Scan -> Index Only Scan com Heap Fetches: 0 e cerca de 35% menos
páginas lidas. Ele SUBSTITUI o índice antigo (mesmas chaves), então o custo de
escrita não aumenta; só cada entrada fica 2 bytes maior.

Troca sem travar as escritas, como seria em produção:
  CREATE INDEX CONCURRENTLY (novo nome) -> DROP INDEX CONCURRENTLY (antigo) -> RENAME.
CONCURRENTLY não pode rodar dentro de uma transação: autocommit_block() do
Alembic sai da transação da migration para esses comandos. Um CREATE INDEX comum
seguraria um lock que bloqueia INSERTs na tabela durante toda a construção.

Revision ID: dfadb3e4bcb7
Revises: dae9a516af06
Create Date: 2026-10-07 23:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "dfadb3e4bcb7"
down_revision: Union[str, Sequence[str], None] = "dae9a516af06"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NOME = "ix_historico_revisoes_flashcard_revisado_em"


def _trocar(include: str) -> None:
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE INDEX CONCURRENTLY {NOME}_novo "
            f"ON historico_revisoes (flashcard_id, revisado_em) {include}"
        )
        op.execute(f"DROP INDEX CONCURRENTLY {NOME}")
        op.execute(f"ALTER INDEX {NOME}_novo RENAME TO {NOME}")


def upgrade() -> None:
    _trocar("INCLUDE (nota)")


def downgrade() -> None:
    _trocar("")
