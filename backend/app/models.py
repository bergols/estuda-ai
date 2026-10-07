"""Modelos SQLAlchemy — espelham o schema criado pela migration inicial.

Decisões explicadas em docs/modelagem.md. Resumo das convenções:
- PK: bigint GENERATED ALWAYS AS IDENTITY (o banco gera o id; ninguém o escolhe).
- Datas: timestamptz (guarda o instante em UTC; converte no fuso da sessão).
- "Enums": text + CHECK em vez de CREATE TYPE ... AS ENUM, porque remover ou
  renomear um valor de ENUM no Postgres é trabalhoso; trocar um CHECK é uma linha.
- FKs: o Postgres NÃO cria índice na coluna da FK automaticamente. Cada FK aqui
  é coberta por um índice (próprio ou como 1ª coluna de um índice composto).
- ON DELETE no banco + passive_deletes=True no ORM: quem apaga os filhos é o
  Postgres, não o Python carregando linha por linha.
"""

from datetime import datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    FetchedValue,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Dimensão dos embeddings do intfloat/multilingual-e5-small (fase 2; a fase 1
# usava 1024). Mudar exige migration + recalcular todos os embeddings + recriar
# o índice HNSW. Ver a migration "embedding_384_dimensoes".
EMBEDDING_DIM = 384

# Nomes previsíveis para constraints. Sem isso o Postgres inventa nomes
# (ex.: disciplinas_usuario_id_fkey) e o Alembic não consegue apagá-las depois
# de forma portátil.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    # Após INSERT/UPDATE, busca via RETURNING as colunas preenchidas pelo banco
    # (id, criado_em, atualizado_em) na mesma ida ao servidor.
    __mapper_args__ = {"eager_defaults": True}


def pk() -> Mapped[int]:
    return mapped_column(BigInteger, Identity(always=True), primary_key=True)


def criado_em() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def atualizado_em() -> Mapped[datetime]:
    # Mantido por um trigger no banco (ver migration): vale também para UPDATEs
    # feitos fora do ORM, como no psql. FetchedValue avisa o SQLAlchemy disso.
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        server_onupdate=FetchedValue(),
    )


class Usuario(Base):
    __tablename__ = "usuarios"

    id: Mapped[int] = pk()
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    criado_em: Mapped[datetime] = criado_em()
    atualizado_em: Mapped[datetime] = atualizado_em()

    disciplinas: Mapped[list["Disciplina"]] = relationship(
        back_populates="usuario", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("length(trim(nome)) > 0", name="nome_nao_vazio"),
        CheckConstraint("position('@' in email) > 1", name="email_formato"),
        # Índice de expressão: "Ana@X.com" e "ana@x.com" são o mesmo e-mail.
        Index("uq_usuarios_email_lower", func.lower(email), unique=True),
    )


class Disciplina(Base):
    __tablename__ = "disciplinas"

    id: Mapped[int] = pk()
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False
    )
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    descricao: Mapped[str | None] = mapped_column(Text)
    criado_em: Mapped[datetime] = criado_em()
    atualizado_em: Mapped[datetime] = atualizado_em()

    usuario: Mapped[Usuario] = relationship(back_populates="disciplinas")
    materiais: Mapped[list["Material"]] = relationship(
        back_populates="disciplina", cascade="all, delete-orphan", passive_deletes=True
    )
    flashcards: Mapped[list["Flashcard"]] = relationship(
        back_populates="disciplina", cascade="all, delete-orphan", passive_deletes=True
    )
    questoes: Mapped[list["Questao"]] = relationship(
        back_populates="disciplina", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("length(trim(nome)) > 0", name="nome_nao_vazio"),
        # Um usuário não repete nome de disciplina (ignorando maiúsculas).
        # Como usuario_id é a 1ª coluna, este índice também atende a FK
        # e a consulta "disciplinas do usuário X".
        Index("uq_disciplinas_usuario_nome", usuario_id, func.lower(nome), unique=True),
    )


class Material(Base):
    __tablename__ = "materiais"

    id: Mapped[int] = pk()
    disciplina_id: Mapped[int] = mapped_column(
        ForeignKey("disciplinas.id", ondelete="CASCADE"), nullable=False
    )
    titulo: Mapped[str] = mapped_column(Text, nullable=False)
    tipo: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pendente")
    nome_arquivo: Mapped[str | None] = mapped_column(Text)
    hash_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    tamanho_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    criado_em: Mapped[datetime] = criado_em()
    atualizado_em: Mapped[datetime] = atualizado_em()

    disciplina: Mapped[Disciplina] = relationship(back_populates="materiais")
    trechos: Mapped[list["Trecho"]] = relationship(
        back_populates="material", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("tipo IN ('pdf', 'anotacao', 'texto')", name="tipo_valido"),
        CheckConstraint(
            "status IN ('pendente', 'processando', 'processado', 'erro')", name="status_valido"
        ),
        CheckConstraint("hash_sha256 ~ '^[0-9a-f]{64}$'", name="hash_formato"),
        CheckConstraint("tamanho_bytes > 0", name="tamanho_positivo"),
        # O mesmo arquivo não entra duas vezes na mesma disciplina.
        # Também serve de índice para a FK disciplina_id.
        UniqueConstraint("disciplina_id", "hash_sha256"),
    )


class Trecho(Base):
    __tablename__ = "trechos"

    id: Mapped[int] = pk()
    material_id: Mapped[int] = mapped_column(
        ForeignKey("materiais.id", ondelete="CASCADE"), nullable=False
    )
    ordem: Mapped[int] = mapped_column(Integer, nullable=False)
    conteudo: Mapped[str] = mapped_column(Text, nullable=False)
    pagina: Mapped[int | None] = mapped_column(Integer)
    num_tokens: Mapped[int | None] = mapped_column(Integer)
    # Nulo até o embedding ser calculado (processo assíncrono na fase 2).
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    criado_em: Mapped[datetime] = criado_em()

    material: Mapped[Material] = relationship(back_populates="trechos")

    __table_args__ = (
        CheckConstraint("ordem >= 0", name="ordem_nao_negativa"),
        CheckConstraint("length(conteudo) > 0", name="conteudo_nao_vazio"),
        # CHECK com NULL resulta em NULL, e NULL não reprova o CHECK:
        # pagina/num_tokens podem ser nulos, mas se vierem precisam ser válidos.
        CheckConstraint("pagina >= 1", name="pagina_positiva"),
        CheckConstraint("num_tokens > 0", name="num_tokens_positivo"),
        UniqueConstraint("material_id", "ordem"),
        # HNSW: grafo em camadas para busca aproximada de vizinhos mais próximos.
        # vector_cosine_ops casa com o operador <=> (distância de cosseno).
        Index(
            "ix_trechos_embedding_hnsw",
            embedding,
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class Flashcard(Base):
    __tablename__ = "flashcards"

    id: Mapped[int] = pk()
    disciplina_id: Mapped[int] = mapped_column(
        ForeignKey("disciplinas.id", ondelete="CASCADE"), nullable=False
    )
    # SET NULL: apagar o material de origem não apaga o flashcard já estudado.
    trecho_id: Mapped[int | None] = mapped_column(ForeignKey("trechos.id", ondelete="SET NULL"))
    frente: Mapped[str] = mapped_column(Text, nullable=False)
    verso: Mapped[str] = mapped_column(Text, nullable=False)
    topico: Mapped[str | None] = mapped_column(Text)
    origem: Mapped[str] = mapped_column(Text, nullable=False, server_default="manual")

    # Estado atual do SM-2 — cópia do resultado da última revisão (desnormalização
    # consciente para a consulta "o que revisar hoje" não precisar varrer revisoes).
    facilidade: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), nullable=False, server_default=text("2.50")
    )
    intervalo_dias: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    repeticoes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    proxima_revisao: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    criado_em: Mapped[datetime] = criado_em()
    atualizado_em: Mapped[datetime] = atualizado_em()

    disciplina: Mapped[Disciplina] = relationship(back_populates="flashcards")
    trecho: Mapped[Trecho | None] = relationship()
    revisoes: Mapped[list["Revisao"]] = relationship(
        back_populates="flashcard", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("length(trim(frente)) > 0", name="frente_nao_vazia"),
        CheckConstraint("length(trim(verso)) > 0", name="verso_nao_vazio"),
        CheckConstraint("origem IN ('manual', 'ia')", name="origem_valida"),
        CheckConstraint("facilidade >= 1.30", name="facilidade_minima"),
        CheckConstraint("intervalo_dias >= 0", name="intervalo_nao_negativo"),
        CheckConstraint("repeticoes >= 0", name="repeticoes_nao_negativas"),
        # Composto: filtra por disciplina e já entrega ordenado por data.
        # Atende "cards da disciplina X com proxima_revisao <= agora".
        Index("ix_flashcards_disciplina_proxima_revisao", disciplina_id, proxima_revisao),
        # Parcial: só indexa linhas com trecho. Necessário para o SET NULL ao
        # apagar um trecho não varrer a tabela inteira.
        Index(
            "ix_flashcards_trecho_id",
            trecho_id,
            postgresql_where=trecho_id.isnot(None),
        ),
    )


class Revisao(Base):
    """Histórico imutável: uma linha por revisão feita (log do SM-2)."""

    __tablename__ = "revisoes"

    id: Mapped[int] = pk()
    flashcard_id: Mapped[int] = mapped_column(
        ForeignKey("flashcards.id", ondelete="CASCADE"), nullable=False
    )
    qualidade: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    facilidade: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)
    intervalo_dias: Mapped[int] = mapped_column(Integer, nullable=False)
    repeticoes: Mapped[int] = mapped_column(Integer, nullable=False)
    proxima_revisao: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revisado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    flashcard: Mapped[Flashcard] = relationship(back_populates="revisoes")

    __table_args__ = (
        CheckConstraint("qualidade BETWEEN 0 AND 5", name="qualidade_0_a_5"),
        CheckConstraint("facilidade >= 1.30", name="facilidade_minima"),
        CheckConstraint("intervalo_dias >= 0", name="intervalo_nao_negativo"),
        CheckConstraint("repeticoes >= 0", name="repeticoes_nao_negativas"),
        CheckConstraint("proxima_revisao >= revisado_em", name="proxima_apos_revisao"),
        Index("ix_revisoes_flashcard_revisado_em", flashcard_id, revisado_em),
    )


class Questao(Base):
    __tablename__ = "questoes"

    id: Mapped[int] = pk()
    disciplina_id: Mapped[int] = mapped_column(
        ForeignKey("disciplinas.id", ondelete="CASCADE"), nullable=False
    )
    trecho_id: Mapped[int | None] = mapped_column(ForeignKey("trechos.id", ondelete="SET NULL"))
    enunciado: Mapped[str] = mapped_column(Text, nullable=False)
    tipo: Mapped[str] = mapped_column(Text, nullable=False)
    # Lista de alternativas, ex.: ["A) ...", "B) ..."]. jsonb em vez de uma tabela
    # própria: alternativas só existem dentro da questão e são lidas sempre juntas.
    alternativas: Mapped[list | None] = mapped_column(JSONB)
    resposta_correta: Mapped[str] = mapped_column(Text, nullable=False)
    explicacao: Mapped[str | None] = mapped_column(Text)
    dificuldade: Mapped[int | None] = mapped_column(SmallInteger)
    topico: Mapped[str | None] = mapped_column(Text)
    origem: Mapped[str] = mapped_column(Text, nullable=False, server_default="manual")
    criado_em: Mapped[datetime] = criado_em()
    atualizado_em: Mapped[datetime] = atualizado_em()

    disciplina: Mapped[Disciplina] = relationship(back_populates="questoes")
    trecho: Mapped[Trecho | None] = relationship()
    tentativas: Mapped[list["Tentativa"]] = relationship(
        back_populates="questao", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("length(trim(enunciado)) > 0", name="enunciado_nao_vazio"),
        CheckConstraint(
            "tipo IN ('multipla_escolha', 'verdadeiro_falso', 'dissertativa')", name="tipo_valido"
        ),
        CheckConstraint("origem IN ('manual', 'ia')", name="origem_valida"),
        CheckConstraint("dificuldade BETWEEN 1 AND 5", name="dificuldade_1_a_5"),
        # Múltipla escolha exige um array com 2+ alternativas; os outros tipos não
        # têm alternativas. CASE aninhado porque o SQL não garante a ordem de
        # avaliação do AND, e jsonb_array_length dá erro se o valor não for array.
        CheckConstraint(
            """CASE WHEN tipo = 'multipla_escolha' THEN
                   CASE WHEN jsonb_typeof(alternativas) = 'array'
                        THEN jsonb_array_length(alternativas) >= 2
                        ELSE false END
               ELSE alternativas IS NULL END""",
            name="alternativas_conforme_tipo",
        ),
        Index("ix_questoes_disciplina_id", disciplina_id),
        Index(
            "ix_questoes_trecho_id",
            trecho_id,
            postgresql_where=trecho_id.isnot(None),
        ),
    )


class Tentativa(Base):
    """Histórico imutável de respostas. Sem usuario_id: o dono já é
    determinado por questao -> disciplina -> usuario (evita dependência transitiva)."""

    __tablename__ = "tentativas"

    id: Mapped[int] = pk()
    questao_id: Mapped[int] = mapped_column(
        ForeignKey("questoes.id", ondelete="CASCADE"), nullable=False
    )
    resposta_dada: Mapped[str] = mapped_column(Text, nullable=False)
    correta: Mapped[bool] = mapped_column(Boolean, nullable=False)
    tempo_ms: Mapped[int | None] = mapped_column(Integer)
    respondida_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    questao: Mapped[Questao] = relationship(back_populates="tentativas")

    __table_args__ = (
        CheckConstraint("tempo_ms >= 0", name="tempo_nao_negativo"),
        Index("ix_tentativas_questao_respondida_em", questao_id, respondida_em),
    )
