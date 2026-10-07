"""geracoes: auditoria de chamadas ao LLM (tokens, custo, duração, status)

Uma linha por geração (pergunta, flashcards ou questões), com sucesso ou não.

1. custo_usd é calculado e GRAVADO no momento da chamada, a partir dos tokens e
   do preço do modelo naquele dia. É a mesma lógica do preço gravado no item de
   um pedido: se a tabela de preços mudar amanhã, o histórico continua dizendo
   quanto aquilo custou de fato. Os tokens também ficam, para recalcular se
   quiser. custo_usd é nullable: modelo sem preço conhecido = custo desconhecido
   (e SUM ignora NULL, ver exercício 3.1).

2. A auditoria NÃO deve sumir quando a disciplina é apagada: o dinheiro foi
   gasto. Mas ela precisa continuar sabendo DE QUEM é. Por isso:
     - usuario_id NOT NULL, FK -> usuarios ON DELETE CASCADE;
     - FK COMPOSTA (disciplina_id, usuario_id) -> disciplinas(id, usuario_id)
       ON DELETE SET NULL (disciplina_id)
   A FK composta impede registrar uma geração na disciplina de outro usuário.
   O "SET NULL (disciplina_id)" (Postgres 15+) anula SÓ essa coluna: um SET NULL
   comum anularia as duas e a linha perderia o dono.
   Alvo exigido: uq_disciplinas_id_usuario_id (redundante com a PK).

3. flashcards.geracao_id e questoes.geracao_id: de qual geração veio cada item
   (proveniência). ON DELETE SET NULL; índices parciais (cards manuais não têm).

Índices: (usuario_id, criado_em) atende o relatório de gastos do usuário
(filtra pelo dono e já percorre em ordem de data) e a FK usuario_id.
disciplina_id tem índice parcial para o SET NULL ao apagar uma disciplina.

Revision ID: 9cb08840b04f
Revises: fbf3e5b1d856
Create Date: 2026-10-07 07:33:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9cb08840b04f"
down_revision: Union[str, Sequence[str], None] = "fbf3e5b1d856"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        op.f("uq_disciplinas_id_usuario_id"), "disciplinas", ["id", "usuario_id"]
    )
    op.create_table(
        "geracoes",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("usuario_id", sa.BigInteger(), nullable=False),
        sa.Column("disciplina_id", sa.BigInteger(), nullable=True),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("modelo", sa.Text(), nullable=False),
        sa.Column("tokens_entrada", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_saida", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("custo_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("duracao_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("chamadas", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("erro_mensagem", sa.Text(), nullable=True),
        sa.Column(
            "criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_geracoes")),
        sa.ForeignKeyConstraint(
            ["usuario_id"], ["usuarios.id"], name=op.f("fk_geracoes_usuario_id_usuarios"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["disciplina_id", "usuario_id"], ["disciplinas.id", "disciplinas.usuario_id"],
            name=op.f("fk_geracoes_disciplina_id_disciplinas"),
            ondelete="SET NULL (disciplina_id)",
        ),
        sa.CheckConstraint(
            "tipo IN ('pergunta', 'flashcards', 'questoes')", name=op.f("ck_geracoes_tipo_valido")
        ),
        sa.CheckConstraint(
            "status IN ('sucesso', 'erro_validacao', 'erro_api')",
            name=op.f("ck_geracoes_status_valido"),
        ),
        sa.CheckConstraint(
            "tokens_entrada >= 0 AND tokens_saida >= 0", name=op.f("ck_geracoes_tokens_nao_negativos")
        ),
        sa.CheckConstraint("custo_usd >= 0", name=op.f("ck_geracoes_custo_nao_negativo")),
        sa.CheckConstraint("duracao_ms >= 0", name=op.f("ck_geracoes_duracao_nao_negativa")),
        # 1 chamada, ou 2 quando a primeira resposta falhou na validação.
        sa.CheckConstraint("chamadas BETWEEN 1 AND 2", name=op.f("ck_geracoes_chamadas_1_ou_2")),
        sa.CheckConstraint(
            "(status = 'sucesso') = (erro_mensagem IS NULL)",
            name=op.f("ck_geracoes_erro_conforme_status"),
        ),
    )
    op.create_index("ix_geracoes_usuario_criado_em", "geracoes", ["usuario_id", "criado_em"])
    op.create_index(
        "ix_geracoes_disciplina_id", "geracoes", ["disciplina_id"],
        postgresql_where=sa.text("disciplina_id IS NOT NULL"),
    )

    for tabela in ("flashcards", "questoes"):
        op.add_column(tabela, sa.Column("geracao_id", sa.BigInteger(), nullable=True))
        op.create_foreign_key(
            op.f(f"fk_{tabela}_geracao_id_geracoes"), tabela, "geracoes", ["geracao_id"], ["id"],
            ondelete="SET NULL",
        )
        op.create_index(
            f"ix_{tabela}_geracao_id", tabela, ["geracao_id"],
            postgresql_where=sa.text("geracao_id IS NOT NULL"),
        )


def downgrade() -> None:
    for tabela in ("flashcards", "questoes"):
        op.drop_index(f"ix_{tabela}_geracao_id", table_name=tabela)
        op.drop_column(tabela, "geracao_id")
    op.drop_table("geracoes")
    op.drop_constraint(op.f("uq_disciplinas_id_usuario_id"), "disciplinas", type_="unique")
