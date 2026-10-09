"""Schemas Pydantic: o contrato da API (entrada e saída).

Validam formato e tamanho antes de chegar ao banco. As regras que NUNCA podem
ser violadas (unicidade, FKs, CHECKs) continuam no Postgres: a API é a
primeira linha de defesa, o banco é a última.
"""

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
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
    # Literal = o mesmo conjunto do CHECK no banco; o OpenAPI vira um enum e o
    # frontend recebe um tipo com os 4 estados, não uma string qualquer.
    tipo: Literal["pdf", "anotacao", "texto"]
    status: Literal["pendente", "processando", "concluido", "erro"]
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


class FocoPeriodo(BaseModel):
    periodo: date  # o dia, ou a segunda-feira da semana
    foco_s: int
    sessoes: int
    concluidas: int


class FocoMetodo(BaseModel):
    metodo: Literal["pomodoro", "bloco", "52_17", "personalizado", "todos"]
    sessoes: int
    concluidas: int
    abandonadas: int
    taxa_conclusao: Decimal | None
    foco_medio_s: int | None


class InterrupcoesSessao(BaseModel):
    sessao_id: int
    iniciada_em: datetime
    dia: date
    metodo: Literal["pomodoro", "bloco", "52_17", "personalizado"]
    status: Literal["concluida", "abandonada"]
    interrupcoes: int
    fora_s: int
    foco_efetivo_s: int
    por_hora: Decimal | None


class AcertoPosSessao(BaseModel):
    grupo: Literal["pomodoro", "bloco", "52_17", "personalizado", "sem_sessao"]
    respostas: int
    acertos: int
    taxa: Decimal | None


class AtualizacaoMVSaida(BaseModel):
    atualizado_em: datetime
    duracao_ms: int


# ------------------------------------------------------------------ fase 6


class TokenSaida(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expira_em: datetime


# ------------------------------------------------------------- sessões de estudo (fase 7)
#
# O app desktop manda LOTES de sessões, cada uma com as suas pausas e eventos, e pode
# mandar o mesmo lote de novo (reenvio depois de timeout ou de horas offline). Aqui só
# formato e tamanho; as regras de cada método ficam nos CHECKs do banco, e uma sessão
# recusada não derruba o lote inteiro (ver servicos/sessoes.py).

MetodoSessao = Literal["pomodoro", "bloco", "52_17", "personalizado"]
StatusSessao = Literal["em_andamento", "concluida", "abandonada"]
Sistema = Literal["windows", "macos", "linux", "web"]
# Datas com fuso obrigatório: "2026-10-08T10:00:00" sem fuso seria ambíguo
Instante = AwareDatetime


class PausaEnvio(BaseModel):
    chave: UUID
    tipo: Literal["curta", "longa", "manual"]
    iniciada_em: Instante
    terminada_em: Instante


class EventoFocoEnvio(BaseModel):
    chave: UUID
    tipo: Literal["saida_janela", "programa_bloqueado", "site_bloqueado", "saida_emergencia"]
    ocorrido_em: Instante
    duracao_s: int | None = Field(default=None, ge=0, le=86_400)
    detalhe: str | None = Field(default=None, min_length=1, max_length=200)


class SessaoEnvio(BaseModel):
    chave: UUID
    metodo: MetodoSessao
    foco_min: int = Field(ge=1, le=240)
    pausa_min: int = Field(ge=0, le=60)
    ciclos: int = Field(ge=1, le=12)
    pausa_longa_min: int | None = Field(default=None, ge=1, le=90)
    ciclos_ate_pausa_longa: int | None = Field(default=None, ge=2, le=12)
    meta: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)] | None = None
    disciplina_id: int | None = None
    sistema: Sistema
    status: StatusSessao
    iniciada_em: Instante
    terminada_em: Instante | None = None
    pausas: list[PausaEnvio] = Field(default=[], max_length=200)
    eventos: list[EventoFocoEnvio] = Field(default=[], max_length=1000)


class LoteSessoes(BaseModel):
    sessoes: list[SessaoEnvio] = Field(min_length=1, max_length=50)


class ResultadoSessao(BaseModel):
    chave: UUID
    # criada: primeira vez; atualizada: mudou de status; sem_mudanca: reenvio (nada a
    # fazer); recusada: violou uma regra (o app não deve reenviar esta sessão)
    resultado: Literal["criada", "atualizada", "sem_mudanca", "recusada"]
    id: int | None = None
    pausas_novas: int = 0
    eventos_novos: int = 0
    # A disciplina não existe (ou não é do usuário): a sessão foi gravada sem ela
    disciplina_descartada: bool = False
    erro: str | None = None


class ResultadoSincronizacao(BaseModel):
    sessoes: list[ResultadoSessao]


class SessaoLer(BaseModel):
    id: int
    chave: UUID
    disciplina_id: int | None
    disciplina: str | None
    metodo: MetodoSessao
    status: StatusSessao
    meta: str | None
    sistema: Sistema
    iniciada_em: datetime
    terminada_em: datetime | None
    dia: date
    duracao_planejada_s: int
    duracao_real_s: int | None
    pausas_s: int
    fora_s: int
    interrupcoes: int
    emergencias: int
    foco_efetivo_s: int | None


# ------------------------------------------------------------------- Spotify (fase 7)


class SpotifyConfig(BaseModel):
    client_id: str
    escopos: str


class SpotifyConectar(BaseModel):
    code: str = Field(min_length=1, max_length=2000)
    # RFC 7636: verifier de 43 a 128 caracteres [A-Za-z0-9-._~]
    code_verifier: str = Field(pattern=r"^[A-Za-z0-9\-._~]{43,128}$")
    redirect_uri: str = Field(max_length=200)


class SpotifyTokenEntrada(BaseModel):
    # O app pede um token novo à força quando o Spotify recusou o guardado (401)
    forcar: bool = False


class SpotifyToken(BaseModel):
    access_token: str
    expira_em: datetime


AlvoPlaylist = Literal["padrao", "metodo", "disciplina", "intervalo"]


class SpotifyPlaylistConfig(BaseModel):
    alvo: AlvoPlaylist
    metodo: Literal["pomodoro", "bloco", "52_17", "personalizado"] | None = None
    disciplina_id: int | None = None
    uri: str = Field(pattern=r"^spotify:(playlist|album|artist):[A-Za-z0-9]{22}$")
    nome: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


NoIntervalo = Literal["pausar", "trocar", "continuar"]


class SpotifyPreferencias(BaseModel):
    no_intervalo: NoIntervalo = "pausar"
    playlists: list[SpotifyPlaylistConfig] = Field(default=[], max_length=100)


class SpotifyEstado(BaseModel):
    conectado: bool
    nome: str | None
    spotify_id: str | None
    conectado_em: datetime | None
    no_intervalo: NoIntervalo
    playlists: list[SpotifyPlaylistConfig]


class PlaylistDoSpotify(BaseModel):
    uri: str
    nome: str
    dono: str | None
    imagem: str | None


# ------------------------------------------------------------- bloqueios (fase 7)

_DOMINIO = re.compile(r"^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def _normalizar_dominio(valor: str) -> str:
    """Aceita o que se cola da barra do navegador ("https://www.YouTube.com/watch?v=x")
    e guarda só o domínio ("youtube.com"). O www volta na hora de bloquear (o app
    bloqueia os dois). A regra final é a mesma do CHECK do banco."""
    d = valor.strip().lower()
    d = re.sub(r"^[a-z][a-z0-9+.-]*://", "", d)  # esquema
    d = re.split(r"[/?#:]", d, maxsplit=1)[0]  # caminho, busca, porta
    d = d.removeprefix("www.").rstrip(".")
    if len(d) > 253 or not _DOMINIO.match(d):
        raise ValueError(f"domínio inválido: {valor!r}")
    return d


Dominio = Annotated[str, AfterValidator(_normalizar_dominio)]
NomePrograma = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100, pattern=r"^[^/\\\x00-\x1f\x7f]+$"),
]


class ProgramasPorSistema(BaseModel):
    """O nome do programa muda com o sistema: "Discord" no Mac, "Discord.exe" no Windows."""

    macos: list[NomePrograma] = Field(default=[], max_length=100)
    windows: list[NomePrograma] = Field(default=[], max_length=100)


class Bloqueios(BaseModel):
    bloquear_sites: bool = False
    bloquear_programas: bool = False
    espera_emergencia_s: int = Field(default=60, ge=10, le=600)
    sites: list[Dominio] = Field(default=[], max_length=200)
    programas: ProgramasPorSistema = ProgramasPorSistema()
