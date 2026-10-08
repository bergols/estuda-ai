"""Analytics com dados CONTROLADOS: cada teste monta um histórico pequeno cujo
resultado é conhecido de antemão (sequência de 5 dias, semana vazia no meio, empate
no ranking...). Datas em 2026, horários de parede de São Paulo (fuso do usuário)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models import (
    Alternativa,
    Disciplina,
    Flashcard,
    Geracao,
    HistoricoRevisao,
    Material,
    Questao,
    Tentativa,
    Trecho,
)
from app.servicos import analytics

BRT = timedelta(hours=-3)


def brt(ano, mes, dia, hora=20, minuto=0) -> datetime:
    """Horário de parede em São Paulo -> instante UTC."""
    return datetime(ano, mes, dia, hora, minuto, tzinfo=UTC) - BRT


@pytest.fixture
def disciplina(session, usuario):
    d = Disciplina(usuario_id=usuario.id, nome="Banco de Dados")
    session.add(d)
    session.flush()
    return d


def card(session, disciplina, frente="Card") -> Flashcard:
    c = Flashcard(disciplina_id=disciplina.id, frente=frente, verso="v")
    session.add(c)
    session.flush()
    return c


def revisar(session, card_id, quando: datetime, nota: int = 4) -> None:
    session.add(HistoricoRevisao(
        flashcard_id=card_id, nota=nota,
        facilidade_anterior=Decimal("2.50"), facilidade_nova=Decimal("2.50"),
        intervalo_anterior=1, intervalo_novo=1, repeticoes_anterior=1, repeticoes_nova=1,
        proxima_revisao_anterior=quando, proxima_revisao_nova=quando + timedelta(days=1),
        revisado_em=quando,
    ))
    session.flush()


def questao(session, disciplina) -> tuple[Questao, Alternativa, Alternativa]:
    certa, errada = Alternativa(letra="A", texto="certa", correta=True), Alternativa(letra="B", texto="errada")
    q = Questao(disciplina_id=disciplina.id, enunciado="Qual?", tipo="multipla_escolha",
                alternativas=[certa, errada])
    session.add(q)
    session.flush()
    return q, certa, errada


def responder(session, q, alternativa, quando):
    session.add(Tentativa(questao_id=q.id, alternativa_id=alternativa.id,
                          correta=alternativa.correta, respondida_em=quando))
    session.flush()


def atualizar(session):
    """As consultas históricas leem a materialized view: refresh antes de consultar."""
    analytics.atualizar_mv(session)


# ------------------------------------------------- 4. sequência (gaps-and-islands)


def test_sequencia_atual_de_exatamente_5_dias_e_maior_de_7(session, usuario, disciplina):
    c = card(session, disciplina)
    for dia in range(1, 8):  # 01 a 07/09: 7 dias seguidos
        revisar(session, c.id, brt(2026, 9, dia))
    # buraco de 08 a 15/09
    for dia in range(16, 21):  # 16 a 20/09: 5 dias seguidos, terminando "hoje"
        revisar(session, c.id, brt(2026, 9, dia))
    revisar(session, c.id, brt(2026, 9, 20, 21))  # 2 revisões no mesmo dia contam 1 dia

    s = analytics.sequencia(
        session, usuario_id=usuario.id, disciplina_id=None, agora=brt(2026, 9, 20, 22)
    )

    assert (s["atual_dias"], s["atual_inicio"], s["atual_fim"]) == (5, date(2026, 9, 16), date(2026, 9, 20))
    assert (s["maior_dias"], s["maior_inicio"], s["maior_fim"]) == (7, date(2026, 9, 1), date(2026, 9, 7))
    assert s["estudou_hoje"] is True
    assert s["dias_estudados"] == 12


def test_sequencia_continua_viva_ate_o_fim_do_dia_seguinte(session, usuario, disciplina):
    c = card(session, disciplina)
    for dia in (10, 11, 12):
        revisar(session, c.id, brt(2026, 9, dia))

    no_dia_13 = analytics.sequencia(session, usuario_id=usuario.id, disciplina_id=None,
                                    agora=brt(2026, 9, 13, 9))
    no_dia_14 = analytics.sequencia(session, usuario_id=usuario.id, disciplina_id=None,
                                    agora=brt(2026, 9, 14, 9))

    assert (no_dia_13["atual_dias"], no_dia_13["estudou_hoje"]) == (3, False)  # ainda dá tempo
    assert no_dia_14["atual_dias"] == 0  # pulou o dia 13: quebrou
    assert no_dia_14["maior_dias"] == 3


def test_dia_da_sequencia_e_o_dia_local_e_nao_o_dia_utc(session, usuario, disciplina):
    """23h30 de 05/10 e 00h30 de 06/10 em São Paulo: UMA hora de diferença, mas DOIS
    dias locais. Em UTC as duas são 06/10 (02h30 e 03h30) e virariam um dia só."""
    c = card(session, disciplina)
    revisar(session, c.id, brt(2026, 10, 5, 23, 30))
    revisar(session, c.id, brt(2026, 10, 6, 0, 30))

    s = analytics.sequencia(session, usuario_id=usuario.id, disciplina_id=None,
                            agora=brt(2026, 10, 6, 12))

    assert s["dias_estudados"] == 2 and s["atual_dias"] == 2


def test_sequencia_sem_nenhuma_revisao(session, usuario, disciplina):
    s = analytics.sequencia(session, usuario_id=usuario.id, disciplina_id=None)
    assert (s["atual_dias"], s["maior_dias"], s["dias_estudados"]) == (0, 0, 0)


# ------------------------------------------- 2. evolução: LAG e média móvel


def test_semana_vazia_no_meio_aparece_e_o_lag_compara_com_ela(session, usuario, disciplina):
    c = card(session, disciplina)
    # semana de 07/09: 4 respostas, 3 certas | semana de 14/09: nada | semana de 21/09: 2 de 2
    for dia, nota in ((8, 4), (9, 4), (10, 5), (11, 1)):
        revisar(session, c.id, brt(2026, 9, dia), nota)
    revisar(session, c.id, brt(2026, 9, 22), 4)
    revisar(session, c.id, brt(2026, 9, 23), 5)
    atualizar(session)

    linhas = analytics.evolucao_semanal(session, usuario_id=usuario.id, disciplina_id=None,
                                        de=date(2026, 9, 7), ate=date(2026, 9, 27))

    resumo = [(str(x["semana"]), x["respostas"], x["taxa"], x["taxa_semana_anterior"]) for x in linhas]
    assert resumo == [
        ("2026-09-07", 4, Decimal("0.7500"), None),
        ("2026-09-14", 0, None, Decimal("0.7500")),  # a semana vazia EXISTE
        ("2026-09-21", 2, Decimal("1.0000"), None),  # comparada com a vazia, não com 07/09
    ]


def test_media_movel_e_razao_das_somas_e_nao_media_das_taxas(session, usuario, disciplina):
    c = card(session, disciplina)
    for _ in range(10):  # dia 01: 10 de 10
        revisar(session, c.id, brt(2026, 9, 1), 5)
    revisar(session, c.id, brt(2026, 9, 3), 1)  # dia 03: 0 de 2
    revisar(session, c.id, brt(2026, 9, 3, 21), 1)
    atualizar(session)

    linhas = analytics.evolucao_diaria(session, usuario_id=usuario.id, disciplina_id=None,
                                       de=date(2026, 9, 1), ate=date(2026, 9, 3))

    dia3 = linhas[-1]
    assert [x["respostas"] for x in linhas] == [10, 0, 2]  # o dia 02, vazio, está na série
    assert dia3["taxa"] == Decimal("0")
    # 10 acertos / 12 respostas = 0,8333 (a média das taxas diárias daria 0,5)
    assert dia3["taxa_media_7d"] == Decimal("0.8333")


def test_media_movel_do_primeiro_dia_ja_enxerga_os_6_dias_anteriores(session, usuario, disciplina):
    c = card(session, disciplina)
    revisar(session, c.id, brt(2026, 9, 1), 5)  # antes do período pedido
    revisar(session, c.id, brt(2026, 9, 5), 1)
    atualizar(session)

    [dia5] = analytics.evolucao_diaria(session, usuario_id=usuario.id, disciplina_id=None,
                                       de=date(2026, 9, 5), ate=date(2026, 9, 5))

    assert dia5["respostas_7d"] == 2 and dia5["taxa_media_7d"] == Decimal("0.5000")


# ------------------------------------------ 1. acerto semanal: GROUPING SETS e N:N


def test_acerto_semanal_separa_fontes_e_soma_no_total(session, usuario, disciplina):
    c = card(session, disciplina)
    revisar(session, c.id, brt(2026, 9, 8), 4)
    revisar(session, c.id, brt(2026, 9, 9), 2)
    q, certa, errada = questao(session, disciplina)
    for alternativa in (certa, certa, errada):
        responder(session, q, alternativa, brt(2026, 9, 10))
    atualizar(session)

    linhas = analytics.acerto_semanal(session, usuario_id=usuario.id, disciplina_id=None,
                                      de=date(2026, 9, 7), ate=date(2026, 9, 13), por="disciplina")

    por_fonte = {x["fonte"]: (x["respostas"], x["acertos"], x["taxa"]) for x in linhas}
    assert por_fonte == {
        "revisao": (2, 1, Decimal("0.5000")),
        "questao": (3, 2, Decimal("0.6667")),
        "total": (5, 3, Decimal("0.6000")),  # GROUPING SETS: a soma das duas fontes
    }


def test_acerto_por_material_nao_conta_duas_vezes_o_mesmo_material(session, usuario, disciplina):
    """Card ligado a 2 trechos da MESMA apostila: conta 1 vez nela. Ligado também a
    outra apostila: conta nas duas (é uma resposta sobre as duas)."""
    apostilas = []
    for i, titulo in enumerate(("Apostila A", "Apostila B")):
        m = Material(disciplina_id=disciplina.id, titulo=titulo, tipo="texto", status="concluido",
                     hash_sha256=str(i) * 64, tamanho_bytes=1)
        session.add(m)
        session.flush()
        trechos = [Trecho(material_id=m.id, disciplina_id=disciplina.id, ordem=o, conteudo="x")
                   for o in range(2)]
        session.add_all(trechos)
        session.flush()
        apostilas.append(trechos)
    c = card(session, disciplina)
    for t in (*apostilas[0], apostilas[1][0]):  # 2 trechos de A + 1 de B
        session.execute(text("INSERT INTO flashcard_trechos VALUES (:c, :t)"), {"c": c.id, "t": t.id})
    revisar(session, c.id, brt(2026, 9, 8), 4)

    linhas = analytics.acerto_semanal(session, usuario_id=usuario.id, disciplina_id=None,
                                      de=date(2026, 9, 7), ate=date(2026, 9, 13), por="material")

    assert {x["grupo"]: x["respostas"] for x in linhas} == {"Apostila A": 1, "Apostila B": 1}


# ---------------------------------------------------- 3. ranking com empates


def test_cards_dificeis_com_empate_dense_rank_x_rank(session, usuario, disciplina):
    cards = {nome: card(session, disciplina, nome) for nome in "ABCD"}
    erros = {"A": 3, "B": 2, "C": 2, "D": 1}
    for nome, n in erros.items():
        for i in range(n):
            revisar(session, cards[nome].id, brt(2026, 9, 1 + i), 1)
        revisar(session, cards[nome].id, brt(2026, 9, 10), 4)
    session.execute(text("UPDATE revisoes SET facilidade = 1.30 WHERE disciplina_id = :d"),
                    {"d": disciplina.id})

    todos = analytics.cards_dificeis(session, usuario_id=usuario.id, disciplina_id=None,
                                     de=None, ate=None, limite=10)
    top2 = analytics.cards_dificeis(session, usuario_id=usuario.id, disciplina_id=None,
                                    de=None, ate=None, limite=2)

    posicoes = {x["frente"]: (x["posicao"], x["posicao_rank"]) for x in todos}
    assert posicoes == {"A": (1, 1), "B": (2, 2), "C": (2, 2), "D": (3, 4)}
    assert sorted(x["frente"] for x in top2) == ["A", "B", "C"]  # empate: 3 cards no "top 2"


# ------------------------------------------- 5 e 6. generate_series: previsão e calendário


def test_previsao_inclui_dias_sem_vencimento_e_atrasados_em_hoje(session, usuario, disciplina):
    agora = brt(2026, 10, 7, 10)
    vencimentos = {"atrasado": brt(2026, 10, 1), "hoje": brt(2026, 10, 7, 22),
                   "amanha": brt(2026, 10, 8), "dia 10": brt(2026, 10, 10)}
    for frente, quando in vencimentos.items():
        c = card(session, disciplina, frente)
        session.execute(text("UPDATE revisoes SET proxima_revisao = :q WHERE flashcard_id = :id"),
                        {"q": quando, "id": c.id})

    linhas = analytics.previsao(session, usuario_id=usuario.id, disciplina_id=None, dias=5, agora=agora)

    assert [(str(x["dia"]), x["cards"], x["atrasados"]) for x in linhas] == [
        ("2026-10-07", 2, 1),  # vence hoje + atrasado
        ("2026-10-08", 1, 0),
        ("2026-10-09", 0, 0),  # dia sem nada: aparece com 0
        ("2026-10-10", 1, 0),
        ("2026-10-11", 0, 0),
    ]


def test_calendario_tem_todos_os_dias_e_niveis_por_quartil(session, usuario, disciplina):
    c = card(session, disciplina)
    for dia, quantidade in ((1, 1), (2, 2), (3, 3), (4, 4)):
        for i in range(quantidade):
            revisar(session, c.id, brt(2026, 9, dia, 20, i))
    atualizar(session)

    linhas = analytics.calendario(session, usuario_id=usuario.id, disciplina_id=None,
                                  de=date(2026, 8, 31), ate=date(2026, 9, 6))

    assert len(linhas) == 7
    assert [(x["dia"].day, x["revisoes"], x["nivel"]) for x in linhas] == [
        (31, 0, 0), (1, 1, 1), (2, 2, 2), (3, 3, 3), (4, 4, 4), (5, 0, 0), (6, 0, 0),
    ]
    assert linhas[0]["dia_semana"] == 1  # 31/08/2026 é segunda-feira


# ------------------------------------------------------- 7. custo acumulado


def test_custos_por_mes_e_tipo_com_acumulados(session, usuario, disciplina):
    def gerar(tipo, quando, custo):
        g = Geracao(usuario_id=usuario.id, disciplina_id=disciplina.id, tipo=tipo,
                    modelo="claude-haiku-4-5-20251001", custo_usd=Decimal(custo),
                    duracao_ms=1, status="sucesso", criado_em=quando)  # só-INSERT: sem UPDATE
        session.add(g)
        session.flush()

    gerar("flashcards", brt(2026, 8, 10), "0.10")
    gerar("pergunta", brt(2026, 8, 20), "0.02")
    gerar("flashcards", brt(2026, 9, 5), "0.30")
    gerar("pergunta", brt(2026, 8, 31, 23, 30), "0.01")  # 31/08 23h30 BRT = setembro em UTC

    linhas = analytics.custos(session, usuario_id=usuario.id, disciplina_id=None, de=None, ate=None)

    assert [(str(x["mes"]), x["tipo"], x["custo_usd"], x["acumulado_tipo"], x["acumulado_total"])
            for x in linhas] == [
        ("2026-08-01", "flashcards", Decimal("0.100000"), Decimal("0.100000"), Decimal("0.130000")),
        ("2026-08-01", "pergunta", Decimal("0.030000"), Decimal("0.030000"), Decimal("0.130000")),
        ("2026-09-01", "flashcards", Decimal("0.300000"), Decimal("0.400000"), Decimal("0.430000")),
    ]


# ------------------------------------------------ 8. materialized view: dado velho


def test_materialized_view_so_mostra_o_novo_depois_do_refresh(session, usuario, disciplina):
    c = card(session, disciplina)
    revisar(session, c.id, brt(2026, 9, 1))
    atualizar(session)
    antes = analytics.atualizado_em(session)
    revisar(session, c.id, brt(2026, 9, 1, 21))  # depois do refresh

    def respostas_do_dia():
        [linha] = analytics.evolucao_diaria(session, usuario_id=usuario.id, disciplina_id=None,
                                            de=date(2026, 9, 1), ate=date(2026, 9, 1))
        return linha["respostas"]

    assert respostas_do_dia() == 1  # a MV ainda não sabe da 2a revisão
    atualizar(session)
    assert respostas_do_dia() == 2
    assert analytics.atualizado_em(session) >= antes


# ---------------------------------------------------------------- rotas


@pytest.mark.parametrize("rota", [
    "/analytics/acerto-semanal", "/analytics/acerto-semanal?por=material",
    "/analytics/evolucao/diaria", "/analytics/evolucao/semanal", "/analytics/cards-dificeis",
    "/analytics/sequencia", "/analytics/previsao", "/analytics/calendario", "/analytics/custos",
])
def test_rotas_respondem(client, headers, rota):
    assert client.get(rota, headers=headers).status_code == 200


def test_calendario_padrao_tem_um_ano(client, headers):
    corpo = client.get("/analytics/calendario", headers=headers).json()
    assert len(corpo["dados"]) == 365 and corpo["atualizado_em"]


@pytest.mark.parametrize("params", [
    "de=2026-09-10&ate=2026-09-01",  # de depois de ate
    "de=2020-01-01&ate=2026-01-01",  # mais de 2 anos de generate_series
])
def test_periodo_invalido_retorna_422(client, headers, params):
    assert client.get(f"/analytics/evolucao/diaria?{params}", headers=headers).status_code == 422


def test_disciplina_de_outro_usuario_retorna_404(client, headers, session, outro_usuario):
    alheia = Disciplina(usuario_id=outro_usuario.id, nome="Alheia")
    session.add(alheia)
    session.flush()
    resposta = client.get(f"/analytics/sequencia?disciplina_id={alheia.id}", headers=headers)
    assert resposta.status_code == 404


def test_atualizar_a_materialized_view_pela_api(client, headers):
    corpo = client.post("/analytics/atualizar", headers=headers).json()
    assert corpo["duracao_ms"] >= 0 and corpo["atualizado_em"]
