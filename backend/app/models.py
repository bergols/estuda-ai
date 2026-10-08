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
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Computed,
    DateTime,
    FetchedValue,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    SmallInteger,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Dimensão dos embeddings do intfloat/multilingual-e5-small (fase 2; a fase 1
# usava 1024). Mudar exige migration + recalcular todos os embeddings + recriar
# o índice HNSW. Ver a migration "embedding_384_dimensoes".
EMBEDDING_DIM = 384

# Configuração de text search criada na migration "busca_textual_em_trechos":
# portuguese + unaccent (busca sem diferenciar acentos).
CONFIG_TEXTO = "portugues_unaccent"

STATUS_MATERIAL = ("pendente", "processando", "concluido", "erro")

# Fase 7: sessões de estudo do app desktop
METODOS_SESSAO = ("pomodoro", "bloco", "52_17", "personalizado")
STATUS_SESSAO = ("em_andamento", "concluida", "abandonada")
SISTEMAS = ("windows", "macos", "linux", "web")
TIPOS_PAUSA = ("curta", "longa", "manual")
TIPOS_EVENTO_FOCO = ("saida_janela", "programa_bloqueado", "site_bloqueado", "saida_emergencia")

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
    # "Hoje" da fila de revisões é calculado neste fuso. Validado por trigger
    # (trg_usuarios_fuso_valido): CHECK não pode consultar pg_timezone_names.
    fuso_horario: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="America/Sao_Paulo"
    )
    # Hash argon2id da senha (nunca a senha). NULL = conta sem login.
    senha_hash: Mapped[str | None] = mapped_column(Text)
    # Vai dentro do JWT; incrementar invalida todos os tokens do usuário.
    versao_token: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    criado_em: Mapped[datetime] = criado_em()
    atualizado_em: Mapped[datetime] = atualizado_em()

    disciplinas: Mapped[list["Disciplina"]] = relationship(
        back_populates="usuario", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("length(trim(nome)) > 0", name="nome_nao_vazio"),
        CheckConstraint("position('@' in email) > 1", name="email_formato"),
        CheckConstraint("senha_hash LIKE '$argon2id$%'", name="senha_hash_argon2id"),
        CheckConstraint("versao_token >= 0", name="versao_token_nao_negativa"),
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
        # Redundante com a PK; alvo da FK composta de geracoes (fase 3).
        UniqueConstraint("id", "usuario_id"),
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
    # Caminho do arquivo no volume de uploads. O PDF em si não vai para o banco.
    caminho_arquivo: Mapped[str | None] = mapped_column(Text)
    erro_mensagem: Mapped[str | None] = mapped_column(Text)
    num_paginas: Mapped[int | None] = mapped_column(Integer)
    processado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    criado_em: Mapped[datetime] = criado_em()
    atualizado_em: Mapped[datetime] = atualizado_em()

    disciplina: Mapped[Disciplina] = relationship(back_populates="materiais")
    trechos: Mapped[list["Trecho"]] = relationship(
        back_populates="material", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("tipo IN ('pdf', 'anotacao', 'texto')", name="tipo_valido"),
        CheckConstraint(
            "status IN ('pendente', 'processando', 'concluido', 'erro')", name="status_valido"
        ),
        CheckConstraint("num_paginas > 0", name="num_paginas_positivo"),
        # "Se A então B" em SQL vira "NOT A OR B":
        CheckConstraint("erro_mensagem IS NULL OR status = 'erro'", name="erro_so_com_status_erro"),
        CheckConstraint("tipo <> 'pdf' OR caminho_arquivo IS NOT NULL", name="pdf_tem_arquivo"),
        CheckConstraint("hash_sha256 ~ '^[0-9a-f]{64}$'", name="hash_formato"),
        CheckConstraint("tamanho_bytes > 0", name="tamanho_positivo"),
        # O mesmo arquivo não entra duas vezes na mesma disciplina.
        # Também serve de índice para a FK disciplina_id.
        UniqueConstraint("disciplina_id", "hash_sha256"),
        # Redundante com a PK (id já é único), mas é o alvo exigido pela FK
        # composta trechos(material_id, disciplina_id).
        UniqueConstraint("id", "disciplina_id"),
    )


class Trecho(Base):
    __tablename__ = "trechos"

    id: Mapped[int] = pk()
    material_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Cópia de materiais.disciplina_id (desnormalização): filtro e índice vetorial
    # na mesma tabela. A FK composta abaixo impede a cópia de divergir.
    disciplina_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ordem: Mapped[int] = mapped_column(Integer, nullable=False)
    conteudo: Mapped[str] = mapped_column(Text, nullable=False)
    pagina: Mapped[int | None] = mapped_column(Integer)
    pagina_fim: Mapped[int | None] = mapped_column(Integer)
    num_tokens: Mapped[int | None] = mapped_column(Integer)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    # Coluna gerada pelo banco a cada INSERT/UPDATE de conteudo. deferred: não é
    # carregada nos SELECTs do ORM (só serve para o índice GIN e o @@).
    conteudo_tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(f"to_tsvector('{CONFIG_TEXTO}'::regconfig, conteudo)", persisted=True),
        nullable=False,
        deferred=True,
    )
    criado_em: Mapped[datetime] = criado_em()

    material: Mapped[Material] = relationship(back_populates="trechos")

    __table_args__ = (
        CheckConstraint("ordem >= 0", name="ordem_nao_negativa"),
        CheckConstraint("length(conteudo) > 0", name="conteudo_nao_vazio"),
        # CHECK com NULL resulta em NULL, e NULL não reprova o CHECK:
        # pagina/num_tokens podem ser nulos, mas se vierem precisam ser válidos.
        CheckConstraint("pagina >= 1", name="pagina_positiva"),
        CheckConstraint("num_tokens > 0", name="num_tokens_positivo"),
        CheckConstraint("pagina_fim >= pagina", name="pagina_fim_valida"),
        UniqueConstraint("material_id", "ordem"),
        ForeignKeyConstraint(
            ["material_id", "disciplina_id"],
            ["materiais.id", "materiais.disciplina_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        Index("ix_trechos_disciplina_id", "disciplina_id"),
        # GIN: índice invertido (lexema -> linhas), atende o operador @@.
        Index("ix_trechos_conteudo_tsv", "conteudo_tsv", postgresql_using="gin"),
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


# Tabelas associativas N:N (fase 3). Sem colunas próprias além das duas FKs,
# então são Table "puras" do SQLAlchemy, usadas como `secondary` nos relacionamentos.
# PK composta = o par não se repete; índice em trecho_id = "cards deste trecho" + FK.
flashcard_trechos = Table(
    "flashcard_trechos",
    Base.metadata,
    Column("flashcard_id", ForeignKey("flashcards.id", ondelete="CASCADE"), primary_key=True),
    Column("trecho_id", ForeignKey("trechos.id", ondelete="CASCADE"), primary_key=True),
    Index("ix_flashcard_trechos_trecho_id", "trecho_id"),
)

questao_trechos = Table(
    "questao_trechos",
    Base.metadata,
    Column("questao_id", ForeignKey("questoes.id", ondelete="CASCADE"), primary_key=True),
    Column("trecho_id", ForeignKey("trechos.id", ondelete="CASCADE"), primary_key=True),
    Index("ix_questao_trechos_trecho_id", "trecho_id"),
)


class Geracao(Base):
    """Auditoria: uma linha por chamada (com ou sem sucesso) ao LLM."""

    __tablename__ = "geracoes"

    id: Mapped[int] = pk()
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False
    )
    # Nula quando a disciplina é apagada: a auditoria do gasto sobrevive.
    disciplina_id: Mapped[int | None] = mapped_column(BigInteger)
    tipo: Mapped[str] = mapped_column(Text, nullable=False)
    modelo: Mapped[str] = mapped_column(Text, nullable=False)
    tokens_entrada: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tokens_saida: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Calculado e gravado no momento da chamada (preço daquele dia), como o preço
    # gravado no item de um pedido. NULL = modelo sem preço conhecido.
    custo_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    duracao_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    chamadas: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="1")
    erro_mensagem: Mapped[str | None] = mapped_column(Text)
    criado_em: Mapped[datetime] = criado_em()

    __table_args__ = (
        # FK composta: impede registrar geração na disciplina de outro usuário.
        # SET NULL (disciplina_id), do Postgres 15+: anula só essa coluna e a
        # linha continua sabendo de quem é.
        ForeignKeyConstraint(
            ["disciplina_id", "usuario_id"],
            ["disciplinas.id", "disciplinas.usuario_id"],
            ondelete="SET NULL (disciplina_id)",
        ),
        CheckConstraint("tipo IN ('pergunta', 'flashcards', 'questoes')", name="tipo_valido"),
        CheckConstraint(
            "status IN ('sucesso', 'erro_validacao', 'erro_api')", name="status_valido"
        ),
        CheckConstraint(
            "tokens_entrada >= 0 AND tokens_saida >= 0", name="tokens_nao_negativos"
        ),
        CheckConstraint("custo_usd >= 0", name="custo_nao_negativo"),
        CheckConstraint("duracao_ms >= 0", name="duracao_nao_negativa"),
        CheckConstraint("chamadas BETWEEN 1 AND 2", name="chamadas_1_ou_2"),
        CheckConstraint("(status = 'sucesso') = (erro_mensagem IS NULL)", name="erro_conforme_status"),
        Index("ix_geracoes_usuario_criado_em", "usuario_id", "criado_em"),
        Index(
            "ix_geracoes_disciplina_id",
            "disciplina_id",
            postgresql_where=text("disciplina_id IS NOT NULL"),
        ),
    )


class Flashcard(Base):
    __tablename__ = "flashcards"

    id: Mapped[int] = pk()
    disciplina_id: Mapped[int] = mapped_column(
        ForeignKey("disciplinas.id", ondelete="CASCADE"), nullable=False
    )
    frente: Mapped[str] = mapped_column(Text, nullable=False)
    verso: Mapped[str] = mapped_column(Text, nullable=False)
    topico: Mapped[str | None] = mapped_column(Text)
    origem: Mapped[str] = mapped_column(Text, nullable=False, server_default="manual")
    # Embedding da frente, para deduplicação (sem índice: busca exata por disciplina).
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    geracao_id: Mapped[int | None] = mapped_column(
        ForeignKey("geracoes.id", ondelete="SET NULL")
    )

    criado_em: Mapped[datetime] = criado_em()
    atualizado_em: Mapped[datetime] = atualizado_em()

    disciplina: Mapped[Disciplina] = relationship(back_populates="flashcards")
    # N:N pelos trechos de origem. passive_deletes: o banco apaga as associações.
    trechos: Mapped[list[Trecho]] = relationship(secondary=flashcard_trechos, passive_deletes=True)
    # Estado do SM-2 (1:1). Criado pelo trigger trg_flashcards_criar_estado no INSERT
    # do card, então o ORM só lê (viewonly).
    estado: Mapped["Revisao"] = relationship(viewonly=True, uselist=False)
    historico: Mapped[list["HistoricoRevisao"]] = relationship(viewonly=True)

    __table_args__ = (
        CheckConstraint("length(trim(frente)) > 0", name="frente_nao_vazia"),
        CheckConstraint("length(trim(verso)) > 0", name="verso_nao_vazio"),
        CheckConstraint("origem IN ('manual', 'ia')", name="origem_valida"),
        # FK disciplina_id + "cards da disciplina X" (deduplicação da fase 3).
        Index("ix_flashcards_disciplina_id", disciplina_id),
        # Redundante com a PK; alvo da FK composta de revisoes (fase 4).
        UniqueConstraint("id", "disciplina_id"),
        Index("ix_flashcards_geracao_id", geracao_id, postgresql_where=geracao_id.isnot(None)),
    )


class Revisao(Base):
    """ESTADO ATUAL do SM-2 de um card (1:1 com flashcards).

    Tabela estreita de propósito: cada revisão faz UPDATE aqui, e no Postgres um
    UPDATE grava uma versão nova da linha inteira (MVCC). Separado de flashcards
    (texto + embedding de 1,5 KB), o UPDATE reescreve ~60 bytes, não ~2 KB.
    """

    __tablename__ = "revisoes"

    # 1:1: a PK é a própria FK para o card.
    flashcard_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # Cópia de flashcards.disciplina_id (FK composta abaixo): a fila filtra sem JOIN.
    disciplina_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    facilidade: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), nullable=False, server_default=text("2.50")
    )
    intervalo_dias: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    repeticoes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Card novo nasce "para revisar agora".
    proxima_revisao: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ultima_revisao_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Controle otimista de concorrência: o cliente manda a versão que viu; o UPDATE
    # só acontece se ela ainda for a atual (WHERE versao = :v) e soma 1.
    versao: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    atualizado_em: Mapped[datetime] = atualizado_em()

    __table_args__ = (
        ForeignKeyConstraint(
            ["flashcard_id", "disciplina_id"],
            ["flashcards.id", "flashcards.disciplina_id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        CheckConstraint("facilidade >= 1.30", name="facilidade_minima"),
        CheckConstraint("intervalo_dias >= 0", name="intervalo_nao_negativo"),
        CheckConstraint("repeticoes >= 0", name="repeticoes_nao_negativas"),
        CheckConstraint("versao >= 0", name="versao_nao_negativa"),
        CheckConstraint(
            "(versao = 0) = (ultima_revisao_em IS NULL)", name="versao_conforme_ultima_revisao"
        ),
        CheckConstraint(
            "ultima_revisao_em IS NULL OR proxima_revisao >= ultima_revisao_em",
            name="proxima_apos_ultima",
        ),
        # Fila do dia: "disciplina = X e proxima_revisao < fim de hoje", em ordem de
        # atraso. Escolha comprovada em docs/experimentos/fila-do-dia.md.
        Index("ix_revisoes_disciplina_proxima_revisao", disciplina_id, proxima_revisao),
    )


class HistoricoRevisao(Base):
    """HISTÓRICO IMUTÁVEL: uma linha por revisão feita, com o estado antes e depois.

    Só INSERT: o trigger trg_historico_revisoes_imutavel recusa UPDATE. É o
    registro do que aconteceu (alimenta o dashboard da fase 5); o estado atual em
    revisoes poderia ser reconstruído reaplicando este histórico.
    """

    __tablename__ = "historico_revisoes"

    id: Mapped[int] = pk()
    flashcard_id: Mapped[int] = mapped_column(
        ForeignKey("flashcards.id", ondelete="CASCADE"), nullable=False
    )
    nota: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    facilidade_anterior: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)
    facilidade_nova: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)
    intervalo_anterior: Mapped[int] = mapped_column(Integer, nullable=False)
    intervalo_novo: Mapped[int] = mapped_column(Integer, nullable=False)
    repeticoes_anterior: Mapped[int] = mapped_column(Integer, nullable=False)
    repeticoes_nova: Mapped[int] = mapped_column(Integer, nullable=False)
    # Quando o card vencia antes desta revisão (atraso = revisado_em - isto).
    proxima_revisao_anterior: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    proxima_revisao_nova: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revisado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("nota BETWEEN 0 AND 5", name="nota_0_a_5"),
        CheckConstraint(
            "facilidade_anterior >= 1.30 AND facilidade_nova >= 1.30", name="facilidades_minimas"
        ),
        CheckConstraint(
            "intervalo_anterior >= 0 AND intervalo_novo >= 0", name="intervalos_nao_negativos"
        ),
        CheckConstraint(
            "repeticoes_anterior >= 0 AND repeticoes_nova >= 0", name="repeticoes_nao_negativas"
        ),
        CheckConstraint("proxima_revisao_nova >= revisado_em", name="proxima_apos_revisao"),
        # INCLUDE (nota): Index Only Scan no ramo de revisões de vw_respostas e no
        # ranking de cards difíceis (fase 5; medido em experimentos/analytics.md).
        Index(
            "ix_historico_revisoes_flashcard_revisado_em",
            flashcard_id,
            revisado_em,
            postgresql_include=["nota"],
        ),
    )


class Questao(Base):
    __tablename__ = "questoes"

    id: Mapped[int] = pk()
    disciplina_id: Mapped[int] = mapped_column(
        ForeignKey("disciplinas.id", ondelete="CASCADE"), nullable=False
    )
    enunciado: Mapped[str] = mapped_column(Text, nullable=False)
    tipo: Mapped[str] = mapped_column(Text, nullable=False)
    # Gabarito de V/F e dissertativa. Em múltipla escolha o gabarito mora em
    # alternativas.correta (uma única fonte da verdade) e esta coluna fica NULL.
    resposta_correta: Mapped[str | None] = mapped_column(Text)
    explicacao: Mapped[str | None] = mapped_column(Text)
    dificuldade: Mapped[int | None] = mapped_column(SmallInteger)
    topico: Mapped[str | None] = mapped_column(Text)
    origem: Mapped[str] = mapped_column(Text, nullable=False, server_default="manual")
    geracao_id: Mapped[int | None] = mapped_column(
        ForeignKey("geracoes.id", ondelete="SET NULL")
    )
    criado_em: Mapped[datetime] = criado_em()
    atualizado_em: Mapped[datetime] = atualizado_em()

    disciplina: Mapped[Disciplina] = relationship(back_populates="questoes")
    alternativas: Mapped[list["Alternativa"]] = relationship(
        back_populates="questao",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Alternativa.letra",
    )
    trechos: Mapped[list[Trecho]] = relationship(secondary=questao_trechos, passive_deletes=True)
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
        CheckConstraint(
            "(tipo = 'multipla_escolha') = (resposta_correta IS NULL)",
            name="gabarito_conforme_tipo",
        ),
        # "Pelo menos 2 alternativas e exatamente 1 correta" envolve várias linhas e
        # não cabe em CHECK: é o constraint trigger adiado ck_questoes_alternativas_validas,
        # criado na migration "alternativas_em_tabela" (o ORM não representa triggers).
        Index("ix_questoes_disciplina_id", disciplina_id),
        Index("ix_questoes_geracao_id", geracao_id, postgresql_where=geracao_id.isnot(None)),
    )


class Alternativa(Base):
    __tablename__ = "alternativas"

    id: Mapped[int] = pk()
    questao_id: Mapped[int] = mapped_column(
        ForeignKey("questoes.id", ondelete="CASCADE"), nullable=False
    )
    letra: Mapped[str] = mapped_column(Text, nullable=False)
    texto: Mapped[str] = mapped_column(Text, nullable=False)
    correta: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    questao: Mapped[Questao] = relationship(back_populates="alternativas")

    __table_args__ = (
        CheckConstraint("letra ~ '^[A-E]$'", name="letra_valida"),
        CheckConstraint("length(trim(texto)) > 0", name="texto_nao_vazio"),
        UniqueConstraint("questao_id", "letra"),
        # Redundante com a PK, mas é o alvo exigido pela FK composta de tentativas.
        UniqueConstraint("questao_id", "id"),
        # Índice único PARCIAL: só as linhas com correta = true entram nele, então
        # cada questão pode ter no máximo uma correta.
        Index("uq_alternativas_uma_correta", questao_id, unique=True, postgresql_where=correta),
    )


class Tentativa(Base):
    """Histórico imutável de respostas. Sem usuario_id: o dono já é
    determinado por questao -> disciplina -> usuario (evita dependência transitiva)."""

    __tablename__ = "tentativas"

    id: Mapped[int] = pk()
    questao_id: Mapped[int] = mapped_column(
        ForeignKey("questoes.id", ondelete="CASCADE"), nullable=False
    )
    # Múltipla escolha: a alternativa escolhida. Outros tipos: resposta_dada.
    alternativa_id: Mapped[int | None] = mapped_column(BigInteger)
    resposta_dada: Mapped[str | None] = mapped_column(Text)
    # Gravado (não derivado de alternativas.correta) de propósito: é o resultado
    # NAQUELE momento; se o gabarito for corrigido depois, o histórico não muda.
    correta: Mapped[bool] = mapped_column(Boolean, nullable=False)
    tempo_ms: Mapped[int | None] = mapped_column(Integer)
    respondida_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    questao: Mapped[Questao] = relationship(back_populates="tentativas")
    alternativa: Mapped["Alternativa | None"] = relationship(viewonly=True)

    __table_args__ = (
        CheckConstraint("tempo_ms >= 0", name="tempo_nao_negativo"),
        CheckConstraint(
            "alternativa_id IS NOT NULL OR resposta_dada IS NOT NULL", name="tem_resposta"
        ),
        # FK composta: a alternativa escolhida tem de ser DESTA questão.
        # Sem ondelete = NO ACTION (ver migration "alternativas_em_tabela").
        ForeignKeyConstraint(
            ["questao_id", "alternativa_id"], ["alternativas.questao_id", "alternativas.id"]
        ),
        Index("ix_tentativas_questao_respondida_em", questao_id, respondida_em),
    )


class AtualizacaoMV(Base):
    """Quando cada materialized view foi atualizada (REFRESH) e quanto demorou.

    As views em si (vw_respostas, mv_respostas_diarias) não são modelos do ORM:
    são lidas com SQL explícito em app/servicos/analytics.py.
    """

    __tablename__ = "atualizacoes_mv"

    nome: Mapped[str] = mapped_column(Text, primary_key=True)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duracao_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (CheckConstraint("duracao_ms >= 0", name="duracao_nao_negativa"),)


class LimiteTaxa(Base):
    """Contador de rate limiting por (chave, janela). Tabela UNLOGGED: sem WAL,
    esvaziada numa queda do servidor (contadores de minutos são descartáveis)."""

    __tablename__ = "limites_taxa"

    chave: Mapped[str] = mapped_column(Text, primary_key=True)
    janela_inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    contagem: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("contagem > 0", name="contagem_positiva"),
        Index("ix_limites_taxa_janela_inicio", "janela_inicio"),
        {"prefixes": ["UNLOGGED"]},
    )


def _duracao(inicio: str, fim: str) -> Computed:
    """Segundos entre duas colunas timestamptz, calculados e guardados pelo banco."""
    return Computed(f"(extract(epoch FROM {fim} - {inicio}))::integer", persisted=True)


class SessaoEstudo(Base):
    """Uma sessão de estudo (fase 7). Mutável: o status vai de em_andamento a
    concluida/abandonada. A `chave` é a de idempotência, gerada pelo app desktop
    (ver a migration "sessoes_de_estudo" e docs/modo-foco.md)."""

    __tablename__ = "sessoes_estudo"

    id: Mapped[int] = pk()
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False
    )
    disciplina_id: Mapped[int | None] = mapped_column(BigInteger)
    chave: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    metodo: Mapped[str] = mapped_column(Text, nullable=False)
    foco_min: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    pausa_min: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    ciclos: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    pausa_longa_min: Mapped[int | None] = mapped_column(SmallInteger)
    ciclos_ate_pausa_longa: Mapped[int | None] = mapped_column(SmallInteger)
    meta: Mapped[str | None] = mapped_column(Text)
    sistema: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    iniciada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    terminada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Colunas geradas: o banco calcula; o ORM nunca as escreve
    duracao_planejada_s: Mapped[int] = mapped_column(
        Integer, Computed("foco_min * 60 * ciclos", persisted=True)
    )
    duracao_real_s: Mapped[int | None] = mapped_column(
        Integer, _duracao("iniciada_em", "terminada_em")
    )
    criado_em: Mapped[datetime] = criado_em()
    atualizado_em: Mapped[datetime] = atualizado_em()

    __table_args__ = (
        ForeignKeyConstraint(
            ["disciplina_id", "usuario_id"],
            ["disciplinas.id", "disciplinas.usuario_id"],
            ondelete="SET NULL (disciplina_id)",
        ),
        UniqueConstraint("usuario_id", "chave"),
        CheckConstraint(
            "metodo IN ('pomodoro', 'bloco', '52_17', 'personalizado')", name="metodo_valido"
        ),
        CheckConstraint("foco_min BETWEEN 1 AND 240", name="foco_min_valido"),
        CheckConstraint("pausa_min BETWEEN 0 AND 60", name="pausa_min_valida"),
        CheckConstraint("ciclos BETWEEN 1 AND 12", name="ciclos_validos"),
        CheckConstraint("pausa_longa_min BETWEEN 1 AND 90", name="pausa_longa_valida"),
        CheckConstraint(
            "ciclos_ate_pausa_longa BETWEEN 2 AND 12", name="ciclos_ate_pausa_longa_validos"
        ),
        CheckConstraint(
            "(pausa_longa_min IS NULL) = (ciclos_ate_pausa_longa IS NULL)",
            name="pausa_longa_completa",
        ),
        CheckConstraint(
            "metodo <> '52_17' OR (foco_min = 52 AND pausa_min = 17)", name="metodo_52_17"
        ),
        CheckConstraint(
            "metodo <> 'bloco' OR (pausa_min = 0 AND ciclos = 1 AND pausa_longa_min IS NULL)",
            name="metodo_bloco",
        ),
        CheckConstraint(
            "meta IS NULL OR length(trim(meta)) BETWEEN 1 AND 200", name="meta_valida"
        ),
        CheckConstraint(
            "sistema IN ('windows', 'macos', 'linux', 'web')", name="sistema_valido"
        ),
        CheckConstraint(
            "status IN ('em_andamento', 'concluida', 'abandonada')", name="status_valido"
        ),
        CheckConstraint(
            "(status = 'em_andamento') = (terminada_em IS NULL)", name="fim_conforme_status"
        ),
        CheckConstraint("terminada_em >= iniciada_em", name="fim_apos_inicio"),
        Index("ix_sessoes_estudo_usuario_iniciada_em", "usuario_id", "iniciada_em"),
        Index(
            "ix_sessoes_estudo_disciplina_id",
            "disciplina_id",
            postgresql_where=text("disciplina_id IS NOT NULL"),
        ),
        # "Última sessão terminada antes desta resposta" (acerto após cada método)
        Index(
            "ix_sessoes_estudo_usuario_terminada_em",
            "usuario_id",
            "terminada_em",
            postgresql_include=["metodo"],
            postgresql_where=text("terminada_em IS NOT NULL"),
        ),
    )


class PausaSessao(Base):
    """Pausa completa de uma sessão (só INSERT: enviada quando termina)."""

    __tablename__ = "pausas_sessao"

    id: Mapped[int] = pk()
    sessao_id: Mapped[int] = mapped_column(
        ForeignKey("sessoes_estudo.id", ondelete="CASCADE"), nullable=False
    )
    chave: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    tipo: Mapped[str] = mapped_column(Text, nullable=False)
    iniciada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    terminada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duracao_s: Mapped[int] = mapped_column(Integer, _duracao("iniciada_em", "terminada_em"))
    recebido_em: Mapped[datetime] = criado_em()

    __table_args__ = (
        UniqueConstraint("sessao_id", "chave"),
        CheckConstraint("tipo IN ('curta', 'longa', 'manual')", name="tipo_valido"),
        CheckConstraint("terminada_em >= iniciada_em", name="fim_apos_inicio"),
    )


class EventoFoco(Base):
    """Evento de foco durante uma sessão (só INSERT)."""

    __tablename__ = "eventos_foco"

    id: Mapped[int] = pk()
    sessao_id: Mapped[int] = mapped_column(
        ForeignKey("sessoes_estudo.id", ondelete="CASCADE"), nullable=False
    )
    chave: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    tipo: Mapped[str] = mapped_column(Text, nullable=False)
    ocorrido_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duracao_s: Mapped[int | None] = mapped_column(Integer)
    detalhe: Mapped[str | None] = mapped_column(Text)
    recebido_em: Mapped[datetime] = criado_em()

    __table_args__ = (
        UniqueConstraint("sessao_id", "chave"),
        CheckConstraint(
            "tipo IN ('saida_janela', 'programa_bloqueado', 'site_bloqueado', 'saida_emergencia')",
            name="tipo_valido",
        ),
        CheckConstraint(
            "(tipo = 'saida_janela') = (duracao_s IS NOT NULL)", name="duracao_so_na_saida"
        ),
        CheckConstraint("duracao_s >= 0", name="duracao_nao_negativa"),
        CheckConstraint(
            "detalhe IS NULL OR length(detalhe) BETWEEN 1 AND 200", name="detalhe_valido"
        ),
    )


class SpotifyConta(Base):
    """Conexão com o Spotify (1:1 com o usuário). Tokens em bytea CIFRADOS na aplicação
    (app/servicos/cifra.py); o banco nunca vê o token em claro."""

    __tablename__ = "spotify_contas"

    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), primary_key=True
    )
    spotify_id: Mapped[str] = mapped_column(Text, nullable=False)
    nome: Mapped[str | None] = mapped_column(Text)
    escopos: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    access_token: Mapped[bytes | None] = mapped_column(LargeBinary)
    access_expira_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    conectado_em: Mapped[datetime] = criado_em()
    atualizado_em: Mapped[datetime] = atualizado_em()

    __table_args__ = (
        CheckConstraint(
            "octet_length(refresh_token) > 29 AND get_byte(refresh_token, 0) BETWEEN 1 AND 255",
            name="refresh_cifrado",
        ),
        CheckConstraint(
            "access_token IS NULL OR octet_length(access_token) > 29", name="access_cifrado"
        ),
        CheckConstraint(
            "(access_token IS NULL) = (access_expira_em IS NULL)", name="access_com_validade"
        ),
    )


class SpotifyPlaylist(Base):
    """Que playlist tocar: a da disciplina, senão a do método, senão a padrão; e a do
    intervalo, quando a preferência é trocar de música na pausa."""

    __tablename__ = "spotify_playlists"

    id: Mapped[int] = pk()
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False
    )
    alvo: Mapped[str] = mapped_column(Text, nullable=False)
    metodo: Mapped[str | None] = mapped_column(Text)
    disciplina_id: Mapped[int | None] = mapped_column(BigInteger)
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    nome: Mapped[str] = mapped_column(Text, nullable=False)
    criado_em: Mapped[datetime] = criado_em()

    __table_args__ = (
        ForeignKeyConstraint(
            ["disciplina_id", "usuario_id"],
            ["disciplinas.id", "disciplinas.usuario_id"],
            ondelete="CASCADE",
        ),
        # NULLS NOT DISTINCT: sem isso, duas linhas 'padrao' (metodo e disciplina NULL)
        # não colidiriam, porque no UNIQUE comum NULL é diferente de NULL
        UniqueConstraint(
            "usuario_id", "alvo", "metodo", "disciplina_id", postgresql_nulls_not_distinct=True
        ),
        CheckConstraint(
            "alvo IN ('padrao', 'metodo', 'disciplina', 'intervalo')", name="alvo_valido"
        ),
        CheckConstraint(
            "metodo IN ('pomodoro', 'bloco', '52_17', 'personalizado')", name="metodo_valido"
        ),
        CheckConstraint(
            "(alvo = 'metodo') = (metodo IS NOT NULL)", name="metodo_so_no_alvo_metodo"
        ),
        CheckConstraint(
            "(alvo = 'disciplina') = (disciplina_id IS NOT NULL)",
            name="disciplina_so_no_alvo_disciplina",
        ),
        CheckConstraint(
            "uri ~ '^spotify:(playlist|album|artist):[A-Za-z0-9]{22}$'", name="uri_valida"
        ),
        CheckConstraint("length(trim(nome)) BETWEEN 1 AND 200", name="nome_valido"),
        Index(
            "ix_spotify_playlists_disciplina_id",
            "disciplina_id",
            postgresql_where=text("disciplina_id IS NOT NULL"),
        ),
    )


class PreferenciasFoco(Base):
    """Preferências do modo foco (1:1 com o usuário)."""

    __tablename__ = "preferencias_foco"

    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), primary_key=True
    )
    spotify_no_intervalo: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="pausar"
    )
    criado_em: Mapped[datetime] = criado_em()
    atualizado_em: Mapped[datetime] = atualizado_em()

    __table_args__ = (
        CheckConstraint(
            "spotify_no_intervalo IN ('pausar', 'trocar', 'continuar')",
            name="spotify_no_intervalo_valido",
        ),
    )
