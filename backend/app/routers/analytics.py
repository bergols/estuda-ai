from datetime import date, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.deps import SessionDep, UsuarioAtual
from app.models import Disciplina
from app.schemas import (
    AcertoPosSessao,
    AcertoSemanal,
    AtualizacaoMVSaida,
    CardDificil,
    CustoMensal,
    DiaCalendario,
    EvolucaoDia,
    EvolucaoSemana,
    FocoMetodo,
    FocoPeriodo,
    InterrupcoesSessao,
    PrevisaoDia,
    Sequencia,
    Serie,
)
from app.servicos import analytics, analytics_foco

router = APIRouter(prefix="/analytics", tags=["analytics"])

DisciplinaQ = Annotated[int | None, Query(description="filtra por disciplina")]
DeQ = Annotated[date | None, Query(description="data inicial (no fuso do usuário)")]
AteQ = Annotated[date | None, Query(description="data final, inclusive (padrão: hoje)")]
# generate_series com limites vindos do cliente pode gerar milhões de linhas.
MAX_DIAS = 731


def _validar(session, usuario, disciplina_id) -> None:
    if disciplina_id is not None and session.scalar(
        select(Disciplina.id).where(Disciplina.id == disciplina_id, Disciplina.usuario_id == usuario.id)
    ) is None:
        raise HTTPException(404, "disciplina não encontrada")


def _periodo(session, usuario, de: date | None, ate: date | None, dias_padrao: int):
    ate = ate or analytics.hoje_local(session, usuario.id)
    de = de or ate - timedelta(days=dias_padrao - 1)
    if de > ate:
        raise HTTPException(422, "'de' precisa ser anterior ou igual a 'ate'")
    if (ate - de).days >= MAX_DIAS:
        raise HTTPException(422, f"período máximo: {MAX_DIAS} dias")
    return de, ate


@router.get("/acerto-semanal", response_model=Serie[AcertoSemanal])
def acerto_semanal(
    usuario: UsuarioAtual, session: SessionDep, disciplina_id: DisciplinaQ = None,
    de: DeQ = None, ate: AteQ = None, por: Literal["disciplina", "material"] = "disciplina",
):
    """Taxa de acerto (revisões com nota >= 3 + questões) por semana e disciplina ou material.

    Padrão: últimas 12 semanas. Por disciplina vem da materialized view (veja atualizado_em).
    """
    _validar(session, usuario, disciplina_id)
    de, ate = _periodo(session, usuario, de, ate, 84)
    dados = analytics.acerto_semanal(
        session, usuario_id=usuario.id, disciplina_id=disciplina_id, de=de, ate=ate, por=por
    )
    return Serie(de=de, ate=ate, dados=dados,
                 atualizado_em=analytics.atualizado_em(session) if por == "disciplina" else None)


@router.get("/evolucao/diaria", response_model=Serie[EvolucaoDia])
def evolucao_diaria(
    usuario: UsuarioAtual, session: SessionDep, disciplina_id: DisciplinaQ = None,
    de: DeQ = None, ate: AteQ = None,
):
    """Taxa de acerto por dia e média móvel de 7 dias. Padrão: últimos 30 dias."""
    _validar(session, usuario, disciplina_id)
    de, ate = _periodo(session, usuario, de, ate, 30)
    dados = analytics.evolucao_diaria(
        session, usuario_id=usuario.id, disciplina_id=disciplina_id, de=de, ate=ate
    )
    return Serie(de=de, ate=ate, dados=dados, atualizado_em=analytics.atualizado_em(session))


@router.get("/evolucao/semanal", response_model=Serie[EvolucaoSemana])
def evolucao_semanal(
    usuario: UsuarioAtual, session: SessionDep, disciplina_id: DisciplinaQ = None,
    de: DeQ = None, ate: AteQ = None,
):
    """Taxa de acerto por semana comparada à semana anterior (LAG). Padrão: 12 semanas."""
    _validar(session, usuario, disciplina_id)
    de, ate = _periodo(session, usuario, de, ate, 84)
    dados = analytics.evolucao_semanal(
        session, usuario_id=usuario.id, disciplina_id=disciplina_id, de=de, ate=ate
    )
    return Serie(de=de, ate=ate, dados=dados, atualizado_em=analytics.atualizado_em(session))


@router.get("/cards-dificeis", response_model=Serie[CardDificil])
def cards_dificeis(
    usuario: UsuarioAtual, session: SessionDep, disciplina_id: DisciplinaQ = None,
    de: DeQ = None, ate: AteQ = None, limite: Annotated[int, Query(ge=1, le=50)] = 5,
):
    """Os cards com mais erros (e menor facilidade) de cada disciplina, com empates
    (DENSE_RANK). Sem período: todo o histórico. Ao vivo."""
    _validar(session, usuario, disciplina_id)
    if de and ate and de > ate:
        raise HTTPException(422, "'de' precisa ser anterior ou igual a 'ate'")
    dados = analytics.cards_dificeis(
        session, usuario_id=usuario.id, disciplina_id=disciplina_id, de=de, ate=ate, limite=limite
    )
    return Serie(de=de, ate=ate, dados=dados)


@router.get("/sequencia", response_model=Sequencia)
def sequencia(usuario: UsuarioAtual, session: SessionDep, disciplina_id: DisciplinaQ = None):
    """Dias seguidos com pelo menos uma revisão (no fuso do usuário): a sequência atual
    e a maior de todas. Ao vivo."""
    _validar(session, usuario, disciplina_id)
    return analytics.sequencia(session, usuario_id=usuario.id, disciplina_id=disciplina_id)


@router.get("/previsao", response_model=Serie[PrevisaoDia])
def previsao(
    usuario: UsuarioAtual, session: SessionDep, disciplina_id: DisciplinaQ = None,
    dias: Annotated[int, Query(ge=1, le=90)] = 30,
):
    """Quantos cards vencem em cada um dos próximos dias (atrasados entram em hoje). Ao vivo."""
    _validar(session, usuario, disciplina_id)
    dados = analytics.previsao(
        session, usuario_id=usuario.id, disciplina_id=disciplina_id, dias=dias
    )
    return Serie(de=dados[0]["dia"], ate=dados[-1]["dia"], dados=dados)


@router.get("/calendario", response_model=Serie[DiaCalendario])
def calendario(
    usuario: UsuarioAtual, session: SessionDep, disciplina_id: DisciplinaQ = None,
    de: DeQ = None, ate: AteQ = None,
):
    """Revisões por dia (calendário estilo GitHub), com nível de 0 a 4. Padrão: 1 ano."""
    _validar(session, usuario, disciplina_id)
    de, ate = _periodo(session, usuario, de, ate, 365)
    dados = analytics.calendario(
        session, usuario_id=usuario.id, disciplina_id=disciplina_id, de=de, ate=ate
    )
    return Serie(de=de, ate=ate, dados=dados, atualizado_em=analytics.atualizado_em(session))


@router.get("/custos", response_model=Serie[CustoMensal])
def custos(
    usuario: UsuarioAtual, session: SessionDep, disciplina_id: DisciplinaQ = None,
    de: DeQ = None, ate: AteQ = None,
):
    """Gasto com IA por mês e tipo de geração, com acumulados. Sem período: tudo. Ao vivo."""
    _validar(session, usuario, disciplina_id)
    if de and ate and de > ate:
        raise HTTPException(422, "'de' precisa ser anterior ou igual a 'ate'")
    dados = analytics.custos(
        session, usuario_id=usuario.id, disciplina_id=disciplina_id, de=de, ate=ate
    )
    return Serie(de=de, ate=ate, dados=dados)


@router.post("/atualizar", response_model=AtualizacaoMVSaida)
def atualizar(usuario: UsuarioAtual, session: SessionDep):
    """REFRESH MATERIALIZED VIEW CONCURRENTLY: traz para o analytics as respostas
    registradas depois do último refresh, sem bloquear quem está lendo."""
    a = analytics.atualizar_mv(session)
    return AtualizacaoMVSaida(atualizado_em=a.atualizado_em, duracao_ms=a.duracao_ms)


# ------------------------------------------------------------- foco (fase 7)
#
# Lidas ao vivo (vw_sessoes_foco), não da materialized view: são poucas linhas por
# usuário (uma por sessão) e a sessão recém-sincronizada deve aparecer na hora.


@router.get("/foco/horas", response_model=Serie[FocoPeriodo])
def foco_horas(
    usuario: UsuarioAtual, session: SessionDep, disciplina_id: DisciplinaQ = None,
    de: DeQ = None, ate: AteQ = None, agrupar: Literal["dia", "semana"] = "dia",
):
    """Foco efetivo (sem pausas nem tempo fora da janela) por dia ou semana.
    Padrão: 30 dias (por dia) ou 12 semanas (por semana)."""
    _validar(session, usuario, disciplina_id)
    de, ate = _periodo(session, usuario, de, ate, 30 if agrupar == "dia" else 84)
    dados = analytics_foco.horas(session, usuario_id=usuario.id, disciplina_id=disciplina_id,
                                 de=de, ate=ate, unidade="day" if agrupar == "dia" else "week")
    return Serie(de=de, ate=ate, dados=dados)


@router.get("/foco/sessoes", response_model=Serie[FocoMetodo])
def foco_sessoes(
    usuario: UsuarioAtual, session: SessionDep, disciplina_id: DisciplinaQ = None,
    de: DeQ = None, ate: AteQ = None,
):
    """Sessões concluídas e abandonadas por método, com a linha 'todos'. Padrão: 12 semanas."""
    _validar(session, usuario, disciplina_id)
    de, ate = _periodo(session, usuario, de, ate, 84)
    dados = analytics_foco.sessoes_por_metodo(session, usuario_id=usuario.id,
                                              disciplina_id=disciplina_id, de=de, ate=ate)
    return Serie(de=de, ate=ate, dados=dados)


@router.get("/foco/interrupcoes", response_model=Serie[InterrupcoesSessao])
def foco_interrupcoes(
    usuario: UsuarioAtual, session: SessionDep, disciplina_id: DisciplinaQ = None,
    de: DeQ = None, ate: AteQ = None, limite: Annotated[int, Query(ge=1, le=200)] = 40,
):
    """Interrupções de cada sessão (as mais recentes do período). Padrão: 30 dias."""
    _validar(session, usuario, disciplina_id)
    de, ate = _periodo(session, usuario, de, ate, 30)
    dados = analytics_foco.interrupcoes(session, usuario_id=usuario.id, disciplina_id=disciplina_id,
                                        de=de, ate=ate, limite=limite)
    return Serie(de=de, ate=ate, dados=dados)


@router.get("/foco/acerto-pos-sessao", response_model=Serie[AcertoPosSessao])
def foco_acerto_pos_sessao(
    usuario: UsuarioAtual, session: SessionDep, disciplina_id: DisciplinaQ = None,
    de: DeQ = None, ate: AteQ = None,
    janela_min: Annotated[int, Query(ge=5, le=240, description="minutos depois do fim da sessão")] = 60,
):
    """Taxa de acerto (cards e questões) nas respostas dadas até `janela_min` minutos
    depois de uma sessão de cada método, e sem sessão antes. Padrão: 12 semanas."""
    _validar(session, usuario, disciplina_id)
    de, ate = _periodo(session, usuario, de, ate, 84)
    dados = analytics_foco.acerto_pos_sessao(session, usuario_id=usuario.id,
                                             disciplina_id=disciplina_id, de=de, ate=ate,
                                             janela_min=janela_min)
    return Serie(de=de, ate=ate, dados=dados)
