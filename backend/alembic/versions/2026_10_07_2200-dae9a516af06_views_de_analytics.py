"""views de analytics: vw_respostas (VIEW) e mv_respostas_diarias (MATERIALIZED VIEW)

VIEW = uma consulta com nome. Não guarda dados: cada SELECT na view roda a
consulta de novo, sobre os dados atuais. Serve para reaproveitar uma consulta
e esconder detalhes (aqui: juntar revisões e tentativas num formato só e
calcular o dia no fuso do usuário, uma vez, em vez de repetir em toda consulta).

MATERIALIZED VIEW = o RESULTADO da consulta gravado em disco, como uma tabela.
Ler é rápido (já está agregado), mas o dado fica parado no instante do último
REFRESH: revisões feitas depois não aparecem até o próximo refresh.

REFRESH MATERIALIZED VIEW (sem CONCURRENTLY) recalcula tudo e troca o conteúdo
segurando um lock ACCESS EXCLUSIVE: ninguém consegue LER a MV durante o refresh.
REFRESH ... CONCURRENTLY calcula o resultado novo, compara com o atual e aplica
só as diferenças (INSERT/UPDATE/DELETE), sem bloquear leitores. Para casar
"linha velha" com "linha nova" ele exige um ÍNDICE ÚNICO na MV, sem WHERE e
sobre colunas (não expressões). É mais lento que o refresh comum.

atualizacoes_mv: quando cada MV foi atualizada, para a API dizer a idade do
dado. Fica numa tabela à parte, e não como now() dentro da MV: com now() em
toda linha, TODAS as linhas mudariam a cada refresh e o CONCURRENTLY teria
de reescrever a MV inteira.

Revision ID: dae9a516af06
Revises: f1d2970537cb
Create Date: 2026-10-07 22:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "dae9a516af06"
down_revision: Union[str, Sequence[str], None] = "f1d2970537cb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE VIEW vw_respostas AS
        -- Revisões de flashcards: acerto = nota >= 3 (o mesmo limite do SM-2).
        SELECT d.usuario_id,
               f.disciplina_id,
               'revisao'::text AS fonte,
               h.flashcard_id AS item_id,
               h.nota >= 3 AS acertou,
               h.nota,
               h.revisado_em AS respondido_em,
               (h.revisado_em AT TIME ZONE u.fuso_horario)::date AS dia
        FROM historico_revisoes AS h
        JOIN flashcards AS f ON f.id = h.flashcard_id
        JOIN disciplinas AS d ON d.id = f.disciplina_id
        JOIN usuarios AS u ON u.id = d.usuario_id
        UNION ALL
        -- Tentativas de questões. UNION ALL (e não UNION): as duas partes nunca têm
        -- linhas iguais (fonte difere), e UNION gastaria um passo para remover
        -- duplicatas que não existem.
        SELECT d.usuario_id,
               q.disciplina_id,
               'questao'::text,
               t.questao_id,
               t.correta,
               NULL::smallint,
               t.respondida_em,
               (t.respondida_em AT TIME ZONE u.fuso_horario)::date
        FROM tentativas AS t
        JOIN questoes AS q ON q.id = t.questao_id
        JOIN disciplinas AS d ON d.id = q.disciplina_id
        JOIN usuarios AS u ON u.id = d.usuario_id
        """
    )
    op.execute(
        """
        CREATE MATERIALIZED VIEW mv_respostas_diarias AS
        SELECT usuario_id, disciplina_id, dia, fonte,
               count(*) AS respostas,
               count(*) FILTER (WHERE acertou) AS acertos
        FROM vw_respostas
        GROUP BY usuario_id, disciplina_id, dia, fonte
        """
    )
    # Exigido pelo REFRESH ... CONCURRENTLY. Começa por usuario_id: também atende
    # todas as leituras da API (sempre filtradas pelo usuário).
    op.execute(
        """
        CREATE UNIQUE INDEX uq_mv_respostas_diarias
        ON mv_respostas_diarias (usuario_id, disciplina_id, dia, fonte)
        """
    )
    op.create_table(
        "atualizacoes_mv",
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duracao_ms", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("nome", name=op.f("pk_atualizacoes_mv")),
        sa.CheckConstraint("duracao_ms >= 0", name=op.f("ck_atualizacoes_mv_duracao_nao_negativa")),
    )
    op.execute(
        "INSERT INTO atualizacoes_mv (nome, atualizado_em, duracao_ms) "
        "VALUES ('mv_respostas_diarias', now(), 0)"
    )


def downgrade() -> None:
    op.drop_table("atualizacoes_mv")
    op.execute("DROP MATERIALIZED VIEW mv_respostas_diarias")
    op.execute("DROP VIEW vw_respostas")
