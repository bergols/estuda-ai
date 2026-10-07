"""Schemas Pydantic: o contrato da API (entrada e saída).

Validam formato e tamanho antes de chegar ao banco. As regras que NUNCA podem
ser violadas (unicidade, FKs, CHECKs) continuam no Postgres: a API é a
primeira linha de defesa, o banco é a última.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

Nome = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Descricao = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


class UsuarioCriar(BaseModel):
    nome: Nome
    email: EmailStr


class UsuarioLer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nome: str
    email: str
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
