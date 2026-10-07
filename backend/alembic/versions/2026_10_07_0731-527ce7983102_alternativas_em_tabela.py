"""alternativas em tabela própria (sai o JSONB) + tentativas.alternativa_id

POR QUE TABELA E NÃO JSONB (comparação completa em docs/geracao-llm.md):
  - tentativas precisa apontar para A ALTERNATIVA ESCOLHIDA. Com tabela, isso é
    uma FK; com JSONB seria um índice/letra solto que o banco não confere.
  - FK COMPOSTA tentativas(questao_id, alternativa_id) -> alternativas(questao_id, id):
    impossível registrar como resposta da questão X uma alternativa da questão Y.
    Alvo exigido: UNIQUE (questao_id, id), redundante com a PK, como na fase 2.
  - "No máximo uma correta": índice ÚNICO PARCIAL (questao_id) WHERE correta.
  - Análise de distratores = GROUP BY alternativa_id.

O QUE NÃO CABE EM CHECK: "pelo menos 2 alternativas e exatamente 1 correta"
envolve VÁRIAS LINHAS de alternativas; CHECK só enxerga a linha atual. Vira um
CONSTRAINT TRIGGER ... DEFERRABLE INITIALLY DEFERRED: a verificação fica
pendente e roda no COMMIT. Precisa ser adiada porque, no meio da transação,
a questão passa por estados "inválidos" inevitáveis (acabou de ser inserida e
ainda não tem alternativas). O que importa é o estado no COMMIT.

O gabarito de múltipla escolha passa a morar em alternativas.correta, então
questoes.resposta_correta fica nula para esse tipo (CHECK amarra isso), e
uma única fonte da verdade evita as duas divergirem.

Revision ID: 527ce7983102
Revises: 747e29a161f5
Create Date: 2026-10-07 07:31:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "527ce7983102"
down_revision: Union[str, Sequence[str], None] = "747e29a161f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CHECK_ANTIGO = """CASE WHEN tipo = 'multipla_escolha' THEN
                   CASE WHEN jsonb_typeof(alternativas) = 'array'
                        THEN jsonb_array_length(alternativas) >= 2
                        ELSE false END
               ELSE alternativas IS NULL END"""


def upgrade() -> None:
    op.create_table(
        "alternativas",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("questao_id", sa.BigInteger(), nullable=False),
        sa.Column("letra", sa.Text(), nullable=False),
        sa.Column("texto", sa.Text(), nullable=False),
        sa.Column("correta", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alternativas")),
        sa.ForeignKeyConstraint(
            ["questao_id"], ["questoes.id"], name=op.f("fk_alternativas_questao_id_questoes"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("letra ~ '^[A-E]$'", name=op.f("ck_alternativas_letra_valida")),
        sa.CheckConstraint("length(trim(texto)) > 0", name=op.f("ck_alternativas_texto_nao_vazio")),
        # Também é o índice da FK questao_id (prefixo) e a ordem natural (A, B, C...).
        sa.UniqueConstraint("questao_id", "letra", name=op.f("uq_alternativas_questao_id_letra")),
        sa.UniqueConstraint("questao_id", "id", name=op.f("uq_alternativas_questao_id_id")),
    )
    op.create_index(
        "uq_alternativas_uma_correta", "alternativas", ["questao_id"], unique=True,
        postgresql_where=sa.text("correta"),
    )

    # Dados: cada elemento do array JSONB vira uma linha. WITH ORDINALITY numera
    # os elementos (1, 2, 3...) e chr(64 + n) transforma em letra (A, B, C...).
    op.execute(
        """
        INSERT INTO alternativas (questao_id, letra, texto, correta)
        SELECT q.id, chr(64 + a.n::int), a.texto,
               a.texto = q.resposta_correta OR chr(64 + a.n::int) = q.resposta_correta
        FROM questoes AS q
        CROSS JOIN LATERAL jsonb_array_elements_text(q.alternativas) WITH ORDINALITY AS a(texto, n)
        WHERE q.tipo = 'multipla_escolha'
        """
    )
    op.drop_constraint(op.f("ck_questoes_alternativas_conforme_tipo"), "questoes", type_="check")
    op.drop_column("questoes", "alternativas")
    op.alter_column("questoes", "resposta_correta", nullable=True)
    op.execute("UPDATE questoes SET resposta_correta = NULL WHERE tipo = 'multipla_escolha'")
    op.create_check_constraint(
        op.f("ck_questoes_gabarito_conforme_tipo"),
        "questoes",
        "(tipo = 'multipla_escolha') = (resposta_correta IS NULL)",
    )

    # tentativas: a alternativa escolhida (nula para outros tipos de questão).
    op.add_column("tentativas", sa.Column("alternativa_id", sa.BigInteger(), nullable=True))
    # MATCH SIMPLE (padrão): se alternativa_id for NULL, a FK não é verificada.
    # ON DELETE NO ACTION (padrão): não dá para apagar uma alternativa já respondida,
    # MAS apagar a questão inteira funciona. NO ACTION confere no fim do comando,
    # quando a cascata já removeu as tentativas; RESTRICT conferiria na hora e
    # poderia falhar dependendo da ordem em que a cascata visita as tabelas.
    op.create_foreign_key(
        op.f("fk_tentativas_questao_id_alternativas"), "tentativas", "alternativas",
        ["questao_id", "alternativa_id"], ["questao_id", "id"],
    )
    op.alter_column("tentativas", "resposta_dada", nullable=True)
    op.create_check_constraint(
        op.f("ck_tentativas_tem_resposta"), "tentativas",
        "alternativa_id IS NOT NULL OR resposta_dada IS NOT NULL",
    )

    op.execute(
        """
        CREATE FUNCTION checar_alternativas_da_questao(p_questao_id bigint) RETURNS void
        LANGUAGE plpgsql AS $$
        DECLARE
            v_tipo text;
            v_total int;
            v_corretas int;
        BEGIN
            SELECT tipo INTO v_tipo FROM questoes WHERE id = p_questao_id;
            IF NOT FOUND THEN
                RETURN;  -- a questão foi apagada na mesma transação
            END IF;
            SELECT count(*), count(*) FILTER (WHERE correta)
              INTO v_total, v_corretas
              FROM alternativas WHERE questao_id = p_questao_id;
            IF v_tipo = 'multipla_escolha' AND (v_total < 2 OR v_corretas <> 1) THEN
                RAISE EXCEPTION 'questão % de múltipla escolha precisa de pelo menos 2 alternativas e exatamente 1 correta (tem % alternativas e % corretas)',
                    p_questao_id, v_total, v_corretas
                    USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_questoes_alternativas_validas';
            ELSIF v_tipo <> 'multipla_escolha' AND v_total > 0 THEN
                RAISE EXCEPTION 'questão % do tipo % não pode ter alternativas', p_questao_id, v_tipo
                    USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_questoes_alternativas_validas';
            END IF;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION trg_verificar_alternativas() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_TABLE_NAME = 'questoes' THEN
                PERFORM checar_alternativas_da_questao(NEW.id);
            ELSE
                IF TG_OP IN ('INSERT', 'UPDATE') THEN
                    PERFORM checar_alternativas_da_questao(NEW.questao_id);
                END IF;
                IF TG_OP IN ('UPDATE', 'DELETE') THEN
                    PERFORM checar_alternativas_da_questao(OLD.questao_id);
                END IF;
            END IF;
            RETURN NULL;  -- trigger AFTER: o valor de retorno é ignorado
        END
        $$
        """
    )
    # CONSTRAINT TRIGGER + DEFERRABLE INITIALLY DEFERRED: o evento entra numa fila e
    # a função roda no COMMIT (ou num SET CONSTRAINTS ... IMMEDIATE).
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER ck_questoes_alternativas_validas
        AFTER INSERT OR UPDATE OF tipo ON questoes
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION trg_verificar_alternativas()
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER ck_alternativas_validas
        AFTER INSERT OR UPDATE OR DELETE ON alternativas
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION trg_verificar_alternativas()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER ck_alternativas_validas ON alternativas")
    op.execute("DROP TRIGGER ck_questoes_alternativas_validas ON questoes")
    op.execute("DROP FUNCTION trg_verificar_alternativas()")
    op.execute("DROP FUNCTION checar_alternativas_da_questao(bigint)")

    op.execute(
        """
        UPDATE tentativas AS t SET resposta_dada = a.letra
        FROM alternativas AS a WHERE a.id = t.alternativa_id AND t.resposta_dada IS NULL
        """
    )
    op.drop_constraint(op.f("ck_tentativas_tem_resposta"), "tentativas", type_="check")
    op.alter_column("tentativas", "resposta_dada", nullable=False)
    op.drop_constraint(op.f("fk_tentativas_questao_id_alternativas"), "tentativas", type_="foreignkey")
    op.drop_column("tentativas", "alternativa_id")

    op.drop_constraint(op.f("ck_questoes_gabarito_conforme_tipo"), "questoes", type_="check")
    op.add_column("questoes", sa.Column("alternativas", postgresql.JSONB(), nullable=True))
    op.execute(
        """
        UPDATE questoes AS q
        SET alternativas = a.lista, resposta_correta = a.correta
        FROM (SELECT questao_id,
                     jsonb_agg(texto ORDER BY letra) AS lista,
                     max(texto) FILTER (WHERE correta) AS correta
              FROM alternativas GROUP BY questao_id) AS a
        WHERE a.questao_id = q.id
        """
    )
    op.alter_column("questoes", "resposta_correta", nullable=False)
    op.create_check_constraint(
        op.f("ck_questoes_alternativas_conforme_tipo"), "questoes", CHECK_ANTIGO
    )
    op.drop_table("alternativas")
