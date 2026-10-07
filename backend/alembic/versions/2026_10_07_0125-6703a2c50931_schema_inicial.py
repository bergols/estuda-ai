"""schema inicial

Cria a extensão pgvector, as 8 tabelas do estuda-ai, suas constraints,
índices e o trigger que mantém atualizado_em.

Escrita à mão (não autogerada) para cada decisão ficar explícita.
`alembic check` confirma que ela bate com app/models.py.

Revision ID: 6703a2c50931
Revises:
Create Date: 2026-10-07 01:25:55.604427

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "6703a2c50931"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIM = 1024

# Tabelas mutáveis recebem o trigger de atualizado_em.
# trechos, revisoes e tentativas são imutáveis (só INSERT), então só têm criado_em.
TABELAS_COM_ATUALIZADO_EM = ("usuarios", "disciplinas", "materiais", "flashcards", "questoes")


def _id() -> sa.Column:
    return sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True)


def _criado_em() -> sa.Column:
    return sa.Column(
        "criado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def _atualizado_em() -> sa.Column:
    return sa.Column(
        "atualizado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def upgrade() -> None:
    # A imagem pgvector/pgvector já tem a extensão instalada no servidor,
    # mas extensões são habilitadas por banco de dados.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ---------------------------------------------------------------- usuarios
    op.create_table(
        "usuarios",
        _id(),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        _criado_em(),
        _atualizado_em(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usuarios")),
        sa.CheckConstraint("length(trim(nome)) > 0", name=op.f("ck_usuarios_nome_nao_vazio")),
        sa.CheckConstraint("position('@' in email) > 1", name=op.f("ck_usuarios_email_formato")),
    )
    # UNIQUE sobre uma expressão só existe como índice (não como constraint).
    op.create_index(
        "uq_usuarios_email_lower", "usuarios", [sa.text("lower(email)")], unique=True
    )

    # ------------------------------------------------------------- disciplinas
    op.create_table(
        "disciplinas",
        _id(),
        sa.Column("usuario_id", sa.BigInteger(), nullable=False),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=True),
        _criado_em(),
        _atualizado_em(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_disciplinas")),
        sa.ForeignKeyConstraint(
            ["usuario_id"],
            ["usuarios.id"],
            name=op.f("fk_disciplinas_usuario_id_usuarios"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("length(trim(nome)) > 0", name=op.f("ck_disciplinas_nome_nao_vazio")),
    )
    # Também cobre a FK usuario_id (1ª coluna do índice).
    op.create_index(
        "uq_disciplinas_usuario_nome",
        "disciplinas",
        ["usuario_id", sa.text("lower(nome)")],
        unique=True,
    )

    # --------------------------------------------------------------- materiais
    op.create_table(
        "materiais",
        _id(),
        sa.Column("disciplina_id", sa.BigInteger(), nullable=False),
        sa.Column("titulo", sa.Text(), nullable=False),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pendente"),
        sa.Column("nome_arquivo", sa.Text(), nullable=True),
        sa.Column("hash_sha256", sa.Text(), nullable=False),
        sa.Column("tamanho_bytes", sa.BigInteger(), nullable=False),
        _criado_em(),
        _atualizado_em(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_materiais")),
        sa.ForeignKeyConstraint(
            ["disciplina_id"],
            ["disciplinas.id"],
            name=op.f("fk_materiais_disciplina_id_disciplinas"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "tipo IN ('pdf', 'anotacao', 'texto')", name=op.f("ck_materiais_tipo_valido")
        ),
        sa.CheckConstraint(
            "status IN ('pendente', 'processando', 'processado', 'erro')",
            name=op.f("ck_materiais_status_valido"),
        ),
        sa.CheckConstraint("hash_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_materiais_hash_formato")),
        sa.CheckConstraint("tamanho_bytes > 0", name=op.f("ck_materiais_tamanho_positivo")),
        # A constraint UNIQUE cria um índice B-tree que também cobre a FK disciplina_id.
        sa.UniqueConstraint(
            "disciplina_id", "hash_sha256", name=op.f("uq_materiais_disciplina_id_hash_sha256")
        ),
    )

    # ----------------------------------------------------------------- trechos
    op.create_table(
        "trechos",
        _id(),
        sa.Column("material_id", sa.BigInteger(), nullable=False),
        sa.Column("ordem", sa.Integer(), nullable=False),
        sa.Column("conteudo", sa.Text(), nullable=False),
        sa.Column("pagina", sa.Integer(), nullable=True),
        sa.Column("num_tokens", sa.Integer(), nullable=True),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=True),
        _criado_em(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trechos")),
        sa.ForeignKeyConstraint(
            ["material_id"],
            ["materiais.id"],
            name=op.f("fk_trechos_material_id_materiais"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("ordem >= 0", name=op.f("ck_trechos_ordem_nao_negativa")),
        sa.CheckConstraint("length(conteudo) > 0", name=op.f("ck_trechos_conteudo_nao_vazio")),
        sa.CheckConstraint("pagina >= 1", name=op.f("ck_trechos_pagina_positiva")),
        sa.CheckConstraint("num_tokens > 0", name=op.f("ck_trechos_num_tokens_positivo")),
        sa.UniqueConstraint("material_id", "ordem", name=op.f("uq_trechos_material_id_ordem")),
    )
    # HNSW (Hierarchical Navigable Small World): busca aproximada de vizinhos.
    #   m = 16              -> conexões por nó no grafo (mais = mais preciso e maior)
    #   ef_construction = 64 -> candidatos avaliados ao inserir (mais = build mais lento)
    # Valores padrão do pgvector; ajustaremos na fase 6 com EXPLAIN ANALYZE.
    # Linhas com embedding NULL simplesmente não entram no índice.
    op.create_index(
        "ix_trechos_embedding_hnsw",
        "trechos",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    # -------------------------------------------------------------- flashcards
    op.create_table(
        "flashcards",
        _id(),
        sa.Column("disciplina_id", sa.BigInteger(), nullable=False),
        sa.Column("trecho_id", sa.BigInteger(), nullable=True),
        sa.Column("frente", sa.Text(), nullable=False),
        sa.Column("verso", sa.Text(), nullable=False),
        sa.Column("topico", sa.Text(), nullable=True),
        sa.Column("origem", sa.Text(), nullable=False, server_default="manual"),
        # Estado atual do SM-2 (cópia da última linha de revisoes).
        sa.Column("facilidade", sa.Numeric(4, 2), nullable=False, server_default=sa.text("2.50")),
        sa.Column("intervalo_dias", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("repeticoes", sa.Integer(), nullable=False, server_default="0"),
        # Card novo já nasce "para revisar agora".
        sa.Column(
            "proxima_revisao",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        _criado_em(),
        _atualizado_em(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_flashcards")),
        sa.ForeignKeyConstraint(
            ["disciplina_id"],
            ["disciplinas.id"],
            name=op.f("fk_flashcards_disciplina_id_disciplinas"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["trecho_id"],
            ["trechos.id"],
            name=op.f("fk_flashcards_trecho_id_trechos"),
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("length(trim(frente)) > 0", name=op.f("ck_flashcards_frente_nao_vazia")),
        sa.CheckConstraint("length(trim(verso)) > 0", name=op.f("ck_flashcards_verso_nao_vazio")),
        sa.CheckConstraint("origem IN ('manual', 'ia')", name=op.f("ck_flashcards_origem_valida")),
        sa.CheckConstraint("facilidade >= 1.30", name=op.f("ck_flashcards_facilidade_minima")),
        sa.CheckConstraint("intervalo_dias >= 0", name=op.f("ck_flashcards_intervalo_nao_negativo")),
        sa.CheckConstraint("repeticoes >= 0", name=op.f("ck_flashcards_repeticoes_nao_negativas")),
    )
    op.create_index(
        "ix_flashcards_disciplina_proxima_revisao",
        "flashcards",
        ["disciplina_id", "proxima_revisao"],
    )
    op.create_index(
        "ix_flashcards_trecho_id",
        "flashcards",
        ["trecho_id"],
        postgresql_where=sa.text("trecho_id IS NOT NULL"),
    )

    # ---------------------------------------------------------------- revisoes
    op.create_table(
        "revisoes",
        _id(),
        sa.Column("flashcard_id", sa.BigInteger(), nullable=False),
        sa.Column("qualidade", sa.SmallInteger(), nullable=False),
        sa.Column("facilidade", sa.Numeric(4, 2), nullable=False),
        sa.Column("intervalo_dias", sa.Integer(), nullable=False),
        sa.Column("repeticoes", sa.Integer(), nullable=False),
        sa.Column("proxima_revisao", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "revisado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_revisoes")),
        sa.ForeignKeyConstraint(
            ["flashcard_id"],
            ["flashcards.id"],
            name=op.f("fk_revisoes_flashcard_id_flashcards"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("qualidade BETWEEN 0 AND 5", name=op.f("ck_revisoes_qualidade_0_a_5")),
        sa.CheckConstraint("facilidade >= 1.30", name=op.f("ck_revisoes_facilidade_minima")),
        sa.CheckConstraint("intervalo_dias >= 0", name=op.f("ck_revisoes_intervalo_nao_negativo")),
        sa.CheckConstraint("repeticoes >= 0", name=op.f("ck_revisoes_repeticoes_nao_negativas")),
        sa.CheckConstraint(
            "proxima_revisao >= revisado_em", name=op.f("ck_revisoes_proxima_apos_revisao")
        ),
    )
    op.create_index(
        "ix_revisoes_flashcard_revisado_em", "revisoes", ["flashcard_id", "revisado_em"]
    )

    # ---------------------------------------------------------------- questoes
    op.create_table(
        "questoes",
        _id(),
        sa.Column("disciplina_id", sa.BigInteger(), nullable=False),
        sa.Column("trecho_id", sa.BigInteger(), nullable=True),
        sa.Column("enunciado", sa.Text(), nullable=False),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("alternativas", postgresql.JSONB(), nullable=True),
        sa.Column("resposta_correta", sa.Text(), nullable=False),
        sa.Column("explicacao", sa.Text(), nullable=True),
        sa.Column("dificuldade", sa.SmallInteger(), nullable=True),
        sa.Column("topico", sa.Text(), nullable=True),
        sa.Column("origem", sa.Text(), nullable=False, server_default="manual"),
        _criado_em(),
        _atualizado_em(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_questoes")),
        sa.ForeignKeyConstraint(
            ["disciplina_id"],
            ["disciplinas.id"],
            name=op.f("fk_questoes_disciplina_id_disciplinas"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["trecho_id"],
            ["trechos.id"],
            name=op.f("fk_questoes_trecho_id_trechos"),
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "length(trim(enunciado)) > 0", name=op.f("ck_questoes_enunciado_nao_vazio")
        ),
        sa.CheckConstraint(
            "tipo IN ('multipla_escolha', 'verdadeiro_falso', 'dissertativa')",
            name=op.f("ck_questoes_tipo_valido"),
        ),
        sa.CheckConstraint("origem IN ('manual', 'ia')", name=op.f("ck_questoes_origem_valida")),
        sa.CheckConstraint(
            "dificuldade BETWEEN 1 AND 5", name=op.f("ck_questoes_dificuldade_1_a_5")
        ),
        sa.CheckConstraint(
            """CASE WHEN tipo = 'multipla_escolha' THEN
                   CASE WHEN jsonb_typeof(alternativas) = 'array'
                        THEN jsonb_array_length(alternativas) >= 2
                        ELSE false END
               ELSE alternativas IS NULL END""",
            name=op.f("ck_questoes_alternativas_conforme_tipo"),
        ),
    )
    op.create_index("ix_questoes_disciplina_id", "questoes", ["disciplina_id"])
    op.create_index(
        "ix_questoes_trecho_id",
        "questoes",
        ["trecho_id"],
        postgresql_where=sa.text("trecho_id IS NOT NULL"),
    )

    # -------------------------------------------------------------- tentativas
    op.create_table(
        "tentativas",
        _id(),
        sa.Column("questao_id", sa.BigInteger(), nullable=False),
        sa.Column("resposta_dada", sa.Text(), nullable=False),
        sa.Column("correta", sa.Boolean(), nullable=False),
        sa.Column("tempo_ms", sa.Integer(), nullable=True),
        sa.Column(
            "respondida_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tentativas")),
        sa.ForeignKeyConstraint(
            ["questao_id"],
            ["questoes.id"],
            name=op.f("fk_tentativas_questao_id_questoes"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("tempo_ms >= 0", name=op.f("ck_tentativas_tempo_nao_negativo")),
    )
    op.create_index(
        "ix_tentativas_questao_respondida_em", "tentativas", ["questao_id", "respondida_em"]
    )

    # ------------------------------------------------- trigger de atualizado_em
    # BEFORE UPDATE altera a linha (NEW) antes de ela ser gravada.
    # now() devolve o início da transação: todas as linhas alteradas na mesma
    # transação recebem o mesmo instante.
    op.execute(
        """
        CREATE FUNCTION definir_atualizado_em() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            NEW.atualizado_em := now();
            RETURN NEW;
        END
        $$
        """
    )
    for tabela in TABELAS_COM_ATUALIZADO_EM:
        op.execute(
            f"""
            CREATE TRIGGER trg_{tabela}_atualizado_em
            BEFORE UPDATE ON {tabela}
            FOR EACH ROW EXECUTE FUNCTION definir_atualizado_em()
            """
        )


def downgrade() -> None:
    # Ordem inversa das dependências: filhos antes dos pais.
    # DROP TABLE leva junto os índices, constraints e triggers da tabela.
    for tabela in (
        "tentativas",
        "questoes",
        "revisoes",
        "flashcards",
        "trechos",
        "materiais",
        "disciplinas",
        "usuarios",
    ):
        op.drop_table(tabela)
    op.execute("DROP FUNCTION definir_atualizado_em()")
    op.execute("DROP EXTENSION IF EXISTS vector")
