"""Schemas Pydantic: o contrato da API (entrada e saída).

Validam formato e tamanho antes de chegar ao banco. As regras que NUNCA podem
ser violadas (unicidade, FKs, CHECKs) continuam no Postgres: a API é a
primeira linha de defesa, o banco é a última.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Nome = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Descricao = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


class UsuarioLer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nome: str
    email: str
    fuso_horario: str
    criado_em: datetime


class DisciplinaCriar(BaseModel):
    nome: Nome
    descricao: Descricao | None = None


class DisciplinaAtualizar(BaseModel):
    """PATCH: só os campos enviados são alterados."""

    nome: Nome | None = None
    descricao: Descricao | None = None

    @field_validator("nome")
    @classmethod
    def nome_nao_nulo(cls, valor: str | None) -> str:
        # Omitir "nome" é permitido; enviar "nome": null não (a coluna é NOT NULL).
        if valor is None:
            raise ValueError("nome não pode ser nulo")
        return valor


class DisciplinaLer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nome: str
    descricao: str | None
    criado_em: datetime
    atualizado_em: datetime


class MaterialLer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    disciplina_id: int
    titulo: str
    tipo: str
    status: str
    nome_arquivo: str | None
    tamanho_bytes: int
    num_paginas: int | None
    erro_mensagem: str | None
    criado_em: datetime
    processado_em: datetime | None


class ResultadoBusca(BaseModel):
    trecho_id: int
    material_id: int
    material_titulo: str
    conteudo: str
    pagina: int | None
    pagina_fim: int | None
    # semantica: similaridade de cosseno (1 = mesmo sentido)
    # textual:   ts_rank_cd (relevância textual, sem limite superior)
    # hibrida:   soma RRF das duas posições
    score: float
    # Só no modo híbrido: posição do trecho em cada lista antes da fusão
    posicao_semantica: int | None = None
    posicao_textual: int | None = None


# ------------------------------------------------------------------ fase 3


class GeracaoResumo(BaseModel):
    """O que a chamada ao LLM custou (linha da tabela geracoes)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    modelo: str
    tokens_entrada: int
    tokens_saida: int
    custo_usd: Decimal | None
    duracao_ms: int
    chamadas: int


class PerguntaEntrada(BaseModel):
    pergunta: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    k: int = Field(default=6, ge=1, le=12)


class Citacao(BaseModel):
    trecho_id: int
    material_id: int
    material_titulo: str
    pagina: int | None
    pagina_fim: int | None
    trecho: str  # início do texto citado, para conferência


class RespostaPergunta(BaseModel):
    resposta: str
    encontrado: bool
    citacoes: list[Citacao]
    geracao: GeracaoResumo | None  # None quando nem foi preciso chamar o LLM


class GerarEntrada(BaseModel):
    """Gerar a partir de UM material inteiro OU de um tema (busca híbrida)."""

    material_id: int | None = None
    tema: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)] | None = None
    quantidade: int = Field(default=8, ge=1, le=20)

    @model_validator(mode="after")
    def material_ou_tema(self):
        if (self.material_id is None) == (self.tema is None):
            raise ValueError("informe exatamente um: material_id ou tema")
        return self


class TrechoOrigem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    material_id: int
    pagina: int | None


class FlashcardLer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    frente: str
    verso: str
    topico: str | None
    origem: str
    geracao_id: int | None
    trechos: list[TrechoOrigem]
    criado_em: datetime


class FlashcardDescartado(BaseModel):
    frente: str
    verso: str
    parecido_com_id: int
    parecido_com_frente: str
    similaridade: float


class FlashcardsGeradosSaida(BaseModel):
    criados: list[FlashcardLer]
    descartados: list[FlashcardDescartado]
    limiar_duplicata: float
    geracao: GeracaoResumo


class AlternativaLer(BaseModel):
    """Sem o campo correta: listar questões não pode entregar o gabarito."""

    model_config = ConfigDict(from_attributes=True)

    letra: str
    texto: str


class QuestaoLer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    enunciado: str
    dificuldade: int | None
    topico: str | None
    alternativas: list[AlternativaLer]
    trechos: list[TrechoOrigem]
    geracao_id: int | None
    criado_em: datetime


class QuestoesGeradasSaida(BaseModel):
    questoes: list[QuestaoLer]
    geracao: GeracaoResumo


class TentativaEntrada(BaseModel):
    alternativa: Literal["A", "B", "C", "D", "E"]
    tempo_ms: int | None = Field(default=None, ge=0, le=3_600_000)


class TentativaResultado(BaseModel):
    tentativa_id: int
    correta: bool
    alternativa_escolhida: str
    alternativa_correta: str
    explicacao: str | None
    tempo_ms: int | None


class GastoMensal(BaseModel):
    disciplina_id: int | None  # None: disciplina apagada (a auditoria sobrevive)
    disciplina_nome: str | None
    mes: date  # primeiro dia do mês, no fuso de São Paulo
    geracoes: int
    tokens_entrada: int
    tokens_saida: int
    custo_usd: Decimal


# ------------------------------------------------------------------ fase 4


class CardDaFila(BaseModel):
    flashcard_id: int
    frente: str
    verso: str
    topico: str | None
    disciplina_id: int
    disciplina_nome: str
    proxima_revisao: datetime
    atraso_dias: Decimal  # 0 se vence ainda hoje, mais tarde
    repeticoes: int
    intervalo_dias: int
    facilidade: Decimal
    versao: int  # devolva no POST /revisoes/{id} (controle otimista)


class FilaDoDia(BaseModel):
    fuso_horario: str
    fim_de_hoje: datetime  # próxima meia-noite no fuso do usuário
    cards: list[CardDaFila]


class RevisaoEntrada(BaseModel):
    nota: int = Field(ge=0, le=5)
    versao: int = Field(ge=0, description="a versao do card que você recebeu na fila")


class EstadoSM2Saida(BaseModel):
    facilidade: Decimal
    intervalo_dias: int
    repeticoes: int


class ResultadoRevisaoSaida(BaseModel):
    flashcard_id: int
    nota: int
    anterior: EstadoSM2Saida
    novo: EstadoSM2Saida
    proxima_revisao: datetime
    versao: int
    historico_id: int


# ------------------------------------------------------------------ fase 5


class AcertoSemanal(BaseModel):
    semana: date  # segunda-feira (fuso do usuário)
    disciplina_id: int
    grupo: str  # nome da disciplina ou título do material
    fonte: Literal["revisao", "questao", "total"]
    respostas: int
    acertos: int
    taxa: Decimal | None  # None = semana sem respostas


class EvolucaoDia(BaseModel):
    dia: date
    respostas: int
    acertos: int
    taxa: Decimal | None
    respostas_7d: int
    taxa_media_7d: Decimal | None


class EvolucaoSemana(BaseModel):
    semana: date
    respostas: int
    acertos: int
    taxa: Decimal | None
    taxa_semana_anterior: Decimal | None
    variacao_pp: Decimal | None  # pontos percentuais
    variacao_respostas: int | None


class CardDificil(BaseModel):
    disciplina_id: int
    disciplina: str
    posicao: int  # DENSE_RANK
    posicao_rank: int  # RANK (para comparar)
    flashcard_id: int
    frente: str
    topico: str | None
    revisoes: int
    erros: int
    taxa_erro: Decimal
    facilidade: Decimal


class Sequencia(BaseModel):
    hoje: date
    atual_dias: int
    atual_inicio: date | None
    atual_fim: date | None
    estudou_hoje: bool
    maior_dias: int
    maior_inicio: date | None
    maior_fim: date | None
    dias_estudados: int


class PrevisaoDia(BaseModel):
    dia: date
    cards: int
    atrasados: int  # só no primeiro dia: vencidos antes de hoje


class DiaCalendario(BaseModel):
    dia: date
    dia_semana: int  # 1 = segunda ... 7 = domingo
    revisoes: int
    nivel: int  # 0 a 4 (quartis dos dias com atividade)


class CustoMensal(BaseModel):
    mes: date
    tipo: str
    geracoes: int
    falhas: int
    tokens_entrada: int
    tokens_saida: int
    custo_usd: Decimal
    acumulado_tipo: Decimal
    acumulado_total: Decimal


class Serie[T](BaseModel):
    """Resposta padrão: o período usado e os dados prontos para plotar.

    atualizado_em vem preenchido quando os dados saem da materialized view:
    é o instante do último REFRESH (revisões feitas depois não aparecem ainda).
    """

    de: date | None = None
    ate: date | None = None
    atualizado_em: datetime | None = None
    dados: list[T]


class AtualizacaoMVSaida(BaseModel):
    atualizado_em: datetime
    duracao_ms: int


# ------------------------------------------------------------------ fase 6


class TokenSaida(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expira_em: datetime
