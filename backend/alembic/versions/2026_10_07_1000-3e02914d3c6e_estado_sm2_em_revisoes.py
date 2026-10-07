"""SM-2: estado atual em revisoes (1:1) e histórico imutável em historico_revisoes

ANTES (fases 1-3):
  flashcards.{facilidade, intervalo_dias, repeticoes, proxima_revisao} = estado atual
  revisoes = histórico (uma linha por revisão feita)

DEPOIS:
  revisoes            = estado atual, 1:1 com o card (flashcard_id é PK E FK)
  historico_revisoes  = histórico imutável, com o estado ANTES e DEPOIS de cada revisão

POR QUE TIRAR O ESTADO DE flashcards: no Postgres, UPDATE não altera a linha no
lugar; grava uma VERSÃO NOVA da linha inteira (MVCC) e a antiga vira "tupla
morta" para o VACUUM limpar. Uma linha de flashcards tem frente, verso e um
embedding de 1.544 bytes; cada revisão reescreveria ~2 KB. Numa tabela estreita
(~60 bytes por linha), a mesma revisão reescreve só o estado. Conteúdo (muda
quase nunca) e agendamento (muda a cada revisão) têm ritmos diferentes, e
separá-los em duas tabelas 1:1 é uma "partição vertical".

Passo a passo (preservando os dados):
1. RENAME da tabela revisoes -> historico_revisoes. Renomear a tabela NÃO
   renomeia a sequência da identity, as constraints nem os índices: renomeamos
   cada um, para os nomes continuarem seguindo a convenção.
2. Colunas do histórico ganham sufixo _nova e entram as colunas _anterior,
   preenchidas com LAG() (window function): o "anterior" de uma revisão é o
   "novo" da revisão anterior do mesmo card. Na 1a revisão, os valores iniciais.
3. Trigger que proíbe UPDATE no histórico (só INSERT; DELETE só pela cascata).
4. CREATE TABLE revisoes (estado), preenchida a partir das colunas de flashcards.
   disciplina_id é cópia protegida por FK composta, como em trechos (fase 2),
   para a fila do dia filtrar por disciplina sem JOIN.
5. Trigger AFTER INSERT em flashcards: todo card nasce com seu estado.
6. Remove as colunas do SM-2 de flashcards.

Revision ID: 3e02914d3c6e
Revises: 9cb08840b04f
Create Date: 2026-10-07 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "3e02914d3c6e"
down_revision: Union[str, Sequence[str], None] = "9cb08840b04f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

H = "historico_revisoes"


def upgrade() -> None:
    # ---------------------------------------------------------------- 1. rename
    op.rename_table("revisoes", H)
    op.execute(f"ALTER SEQUENCE revisoes_id_seq RENAME TO {H}_id_seq")
    # Renomear uma constraint baseada em índice (PK, UNIQUE) renomeia o índice junto.
    op.execute(f"ALTER TABLE {H} RENAME CONSTRAINT pk_revisoes TO pk_{H}")
    op.execute(
        f"ALTER TABLE {H} RENAME CONSTRAINT fk_revisoes_flashcard_id_flashcards "
        f"TO fk_{H}_flashcard_id_flashcards"
    )
    op.execute(f"ALTER INDEX ix_revisoes_flashcard_revisado_em RENAME TO ix_{H}_flashcard_revisado_em")
    for ck in ("facilidade_minima", "intervalo_nao_negativo", "proxima_apos_revisao",
               "qualidade_0_a_5", "repeticoes_nao_negativas"):
        op.drop_constraint(op.f(f"ck_revisoes_{ck}"), H, type_="check")

    # --------------------------------------------------- 2. colunas do histórico
    for antiga, nova in (("qualidade", "nota"), ("facilidade", "facilidade_nova"),
                         ("intervalo_dias", "intervalo_novo"), ("repeticoes", "repeticoes_nova"),
                         ("proxima_revisao", "proxima_revisao_nova")):
        op.alter_column(H, antiga, new_column_name=nova)
    op.add_column(H, sa.Column("facilidade_anterior", sa.Numeric(4, 2), nullable=True))
    op.add_column(H, sa.Column("intervalo_anterior", sa.Integer(), nullable=True))
    op.add_column(H, sa.Column("repeticoes_anterior", sa.Integer(), nullable=True))
    op.add_column(H, sa.Column("proxima_revisao_anterior", sa.DateTime(timezone=True), nullable=True))
    # LAG(x) OVER (PARTITION BY card ORDER BY data) = o valor de x na linha
    # anterior do mesmo card. Na primeira revisão não há anterior (NULL), e o
    # coalesce põe o estado inicial do SM-2 (e o card "vencia" desde que nasceu).
    op.execute(
        f"""
        UPDATE {H} AS h
        SET facilidade_anterior = x.facilidade_anterior,
            intervalo_anterior = x.intervalo_anterior,
            repeticoes_anterior = x.repeticoes_anterior,
            proxima_revisao_anterior = x.proxima_revisao_anterior
        FROM (
            SELECT h2.id,
                   coalesce(lag(h2.facilidade_nova) OVER w, 2.50) AS facilidade_anterior,
                   coalesce(lag(h2.intervalo_novo) OVER w, 0) AS intervalo_anterior,
                   coalesce(lag(h2.repeticoes_nova) OVER w, 0) AS repeticoes_anterior,
                   coalesce(lag(h2.proxima_revisao_nova) OVER w, f.criado_em) AS proxima_revisao_anterior
            FROM {H} AS h2
            JOIN flashcards AS f ON f.id = h2.flashcard_id
            WINDOW w AS (PARTITION BY h2.flashcard_id ORDER BY h2.revisado_em, h2.id)
        ) AS x
        WHERE x.id = h.id
        """
    )
    for coluna in ("facilidade_anterior", "intervalo_anterior", "repeticoes_anterior",
                   "proxima_revisao_anterior"):
        op.alter_column(H, coluna, nullable=False)
    op.create_check_constraint(op.f(f"ck_{H}_nota_0_a_5"), H, "nota BETWEEN 0 AND 5")
    op.create_check_constraint(
        op.f(f"ck_{H}_facilidades_minimas"), H,
        "facilidade_anterior >= 1.30 AND facilidade_nova >= 1.30",
    )
    op.create_check_constraint(
        op.f(f"ck_{H}_intervalos_nao_negativos"), H,
        "intervalo_anterior >= 0 AND intervalo_novo >= 0",
    )
    op.create_check_constraint(
        op.f(f"ck_{H}_repeticoes_nao_negativas"), H,
        "repeticoes_anterior >= 0 AND repeticoes_nova >= 0",
    )
    op.create_check_constraint(
        op.f(f"ck_{H}_proxima_apos_revisao"), H, "proxima_revisao_nova >= revisado_em"
    )

    # --------------------------------------------- 3. histórico só aceita INSERT
    op.execute(
        """
        CREATE FUNCTION impedir_alteracao_historico() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'historico_revisoes é imutável: registre uma nova revisão em vez de alterar'
                USING ERRCODE = 'restrict_violation';
        END
        $$
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_{H}_imutavel BEFORE UPDATE ON {H}
        FOR EACH ROW EXECUTE FUNCTION impedir_alteracao_historico()
        """
    )

    # ------------------------------------------------------- 4. estado: revisoes
    op.create_unique_constraint(
        op.f("uq_flashcards_id_disciplina_id"), "flashcards", ["id", "disciplina_id"]
    )
    op.create_table(
        "revisoes",
        # 1:1: a PK é a própria FK. Não existe "segunda linha de estado" para um card.
        sa.Column("flashcard_id", sa.BigInteger(), nullable=False),
        sa.Column("disciplina_id", sa.BigInteger(), nullable=False),
        sa.Column("facilidade", sa.Numeric(4, 2), nullable=False, server_default=sa.text("2.50")),
        sa.Column("intervalo_dias", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("repeticoes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "proxima_revisao", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("ultima_revisao_em", sa.DateTime(timezone=True), nullable=True),
        # Controle otimista de concorrência: +1 a cada revisão.
        sa.Column("versao", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "atualizado_em", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("flashcard_id", name=op.f("pk_revisoes")),
        sa.ForeignKeyConstraint(
            ["flashcard_id", "disciplina_id"], ["flashcards.id", "flashcards.disciplina_id"],
            name=op.f("fk_revisoes_flashcard_id_flashcards"),
            ondelete="CASCADE", onupdate="CASCADE",
        ),
        sa.CheckConstraint("facilidade >= 1.30", name=op.f("ck_revisoes_facilidade_minima")),
        sa.CheckConstraint("intervalo_dias >= 0", name=op.f("ck_revisoes_intervalo_nao_negativo")),
        sa.CheckConstraint("repeticoes >= 0", name=op.f("ck_revisoes_repeticoes_nao_negativas")),
        sa.CheckConstraint("versao >= 0", name=op.f("ck_revisoes_versao_nao_negativa")),
        # versao 0 <=> nunca revisado <=> sem data de última revisão
        sa.CheckConstraint(
            "(versao = 0) = (ultima_revisao_em IS NULL)",
            name=op.f("ck_revisoes_versao_conforme_ultima_revisao"),
        ),
        sa.CheckConstraint(
            "ultima_revisao_em IS NULL OR proxima_revisao >= ultima_revisao_em",
            name=op.f("ck_revisoes_proxima_apos_ultima"),
        ),
    )
    # Índice da fila do dia: disciplina = X e proxima_revisao <= fim de hoje, já em
    # ordem de atraso. Escolha comprovada em docs/experimentos/fila-do-dia.md.
    op.create_index(
        "ix_revisoes_disciplina_proxima_revisao", "revisoes", ["disciplina_id", "proxima_revisao"]
    )
    op.execute(
        """
        CREATE TRIGGER trg_revisoes_atualizado_em BEFORE UPDATE ON revisoes
        FOR EACH ROW EXECUTE FUNCTION definir_atualizado_em()
        """
    )
    op.execute(
        f"""
        INSERT INTO revisoes (flashcard_id, disciplina_id, facilidade, intervalo_dias, repeticoes,
                              proxima_revisao, ultima_revisao_em, versao)
        SELECT f.id, f.disciplina_id, f.facilidade, f.intervalo_dias, f.repeticoes,
               f.proxima_revisao, h.ultima, coalesce(h.quantas, 0)
        FROM flashcards AS f
        LEFT JOIN (SELECT flashcard_id, max(revisado_em) AS ultima, count(*) AS quantas
                   FROM {H} GROUP BY flashcard_id) AS h ON h.flashcard_id = f.id
        """
    )

    # ------------------------------------------- 5. todo card nasce com estado
    op.execute(
        """
        CREATE FUNCTION criar_estado_revisao() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            INSERT INTO revisoes (flashcard_id, disciplina_id) VALUES (NEW.id, NEW.disciplina_id);
            RETURN NULL;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_flashcards_criar_estado AFTER INSERT ON flashcards
        FOR EACH ROW EXECUTE FUNCTION criar_estado_revisao()
        """
    )

    # ------------------------------------------- 6. tira o SM-2 de flashcards
    op.drop_index("ix_flashcards_disciplina_proxima_revisao", table_name="flashcards")
    # A FK disciplina_id (e a deduplicação da fase 3) usavam esse índice pelo prefixo.
    op.create_index("ix_flashcards_disciplina_id", "flashcards", ["disciplina_id"])
    for ck in ("facilidade_minima", "intervalo_nao_negativo", "repeticoes_nao_negativas"):
        op.drop_constraint(op.f(f"ck_flashcards_{ck}"), "flashcards", type_="check")
    for coluna in ("facilidade", "intervalo_dias", "repeticoes", "proxima_revisao"):
        op.drop_column("flashcards", coluna)


def downgrade() -> None:
    op.add_column("flashcards", sa.Column("facilidade", sa.Numeric(4, 2), nullable=False, server_default=sa.text("2.50")))
    op.add_column("flashcards", sa.Column("intervalo_dias", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("flashcards", sa.Column("repeticoes", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("flashcards", sa.Column("proxima_revisao", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.execute(
        """
        UPDATE flashcards AS f
        SET facilidade = r.facilidade, intervalo_dias = r.intervalo_dias,
            repeticoes = r.repeticoes, proxima_revisao = r.proxima_revisao
        FROM revisoes AS r WHERE r.flashcard_id = f.id
        """
    )
    op.create_check_constraint(op.f("ck_flashcards_facilidade_minima"), "flashcards", "facilidade >= 1.30")
    op.create_check_constraint(op.f("ck_flashcards_intervalo_nao_negativo"), "flashcards", "intervalo_dias >= 0")
    op.create_check_constraint(op.f("ck_flashcards_repeticoes_nao_negativas"), "flashcards", "repeticoes >= 0")
    op.drop_index("ix_flashcards_disciplina_id", table_name="flashcards")
    op.create_index("ix_flashcards_disciplina_proxima_revisao", "flashcards", ["disciplina_id", "proxima_revisao"])

    op.execute("DROP TRIGGER trg_flashcards_criar_estado ON flashcards")
    op.execute("DROP FUNCTION criar_estado_revisao()")
    op.drop_table("revisoes")
    op.drop_constraint(op.f("uq_flashcards_id_disciplina_id"), "flashcards", type_="unique")

    op.execute(f"DROP TRIGGER trg_{H}_imutavel ON {H}")
    op.execute("DROP FUNCTION impedir_alteracao_historico()")
    for ck in ("nota_0_a_5", "facilidades_minimas", "intervalos_nao_negativos",
               "repeticoes_nao_negativas", "proxima_apos_revisao"):
        op.drop_constraint(op.f(f"ck_{H}_{ck}"), H, type_="check")
    for coluna in ("facilidade_anterior", "intervalo_anterior", "repeticoes_anterior",
                   "proxima_revisao_anterior"):
        op.drop_column(H, coluna)
    for antiga, nova in (("qualidade", "nota"), ("facilidade", "facilidade_nova"),
                         ("intervalo_dias", "intervalo_novo"), ("repeticoes", "repeticoes_nova"),
                         ("proxima_revisao", "proxima_revisao_nova")):
        op.alter_column(H, nova, new_column_name=antiga)
    op.execute(f"ALTER INDEX ix_{H}_flashcard_revisado_em RENAME TO ix_revisoes_flashcard_revisado_em")
    op.execute(f"ALTER TABLE {H} RENAME CONSTRAINT fk_{H}_flashcard_id_flashcards TO fk_revisoes_flashcard_id_flashcards")
    op.execute(f"ALTER TABLE {H} RENAME CONSTRAINT pk_{H} TO pk_revisoes")
    op.execute(f"ALTER SEQUENCE {H}_id_seq RENAME TO revisoes_id_seq")
    op.rename_table(H, "revisoes")
    op.create_check_constraint(op.f("ck_revisoes_qualidade_0_a_5"), "revisoes", "qualidade BETWEEN 0 AND 5")
    op.create_check_constraint(op.f("ck_revisoes_facilidade_minima"), "revisoes", "facilidade >= 1.30")
    op.create_check_constraint(op.f("ck_revisoes_intervalo_nao_negativo"), "revisoes", "intervalo_dias >= 0")
    op.create_check_constraint(op.f("ck_revisoes_repeticoes_nao_negativas"), "revisoes", "repeticoes >= 0")
    op.create_check_constraint(op.f("ck_revisoes_proxima_apos_revisao"), "revisoes", "proxima_revisao >= revisado_em")
