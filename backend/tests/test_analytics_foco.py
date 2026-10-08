"""Analytics das sessões de estudo (fase 7) com dados CONTROLADOS: cada teste monta um
punhado de sessões e respostas cujo resultado é conhecido de antemão. Horários de
parede de São Paulo (fuso do usuário)."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.models import Disciplina, EventoFoco, PausaSessao, SessaoEstudo
from app.servicos import analytics_foco
from tests.test_analytics import brt, card, questao, responder, revisar

DE, ATE = date(2026, 9, 1), date(2026, 9, 30)


@pytest.fixture
def disciplina(session, usuario):
    d = Disciplina(usuario_id=usuario.id, nome="Cálculo II")
    session.add(d)
    session.flush()
    return d


def sessao(session, usuario, inicio, minutos=25, *, metodo="pomodoro", status="concluida",
           disciplina_id=None, pausas_min=0, fora_s=0, programas=0) -> SessaoEstudo:
    s = SessaoEstudo(
        usuario_id=usuario.id, disciplina_id=disciplina_id, chave=uuid4(), metodo=metodo,
        foco_min=52 if metodo == "52_17" else 25, pausa_min=17 if metodo == "52_17" else (0 if metodo == "bloco" else 5),
        ciclos=1, sistema="macos", status=status, iniciada_em=inicio,
        terminada_em=None if status == "em_andamento" else inicio + timedelta(minutes=minutos),
    )
    session.add(s)
    session.flush()
    if pausas_min:
        session.add(PausaSessao(sessao_id=s.id, chave=uuid4(), tipo="curta", iniciada_em=inicio,
                                terminada_em=inicio + timedelta(minutes=pausas_min)))
    if fora_s:
        session.add(EventoFoco(sessao_id=s.id, chave=uuid4(), tipo="saida_janela", ocorrido_em=inicio,
                               duracao_s=fora_s))
    for _ in range(programas):
        session.add(EventoFoco(sessao_id=s.id, chave=uuid4(), tipo="programa_bloqueado",
                               ocorrido_em=inicio, detalhe="Discord"))
    session.flush()
    return s


def _horas(session, usuario, unidade="day", **kw):
    return analytics_foco.horas(session, usuario_id=usuario.id, disciplina_id=kw.get("d"),
                                de=kw.get("de", DE), ate=kw.get("ate", ATE), unidade=unidade)


# ------------------------------------------------------------- 1. horas de foco


def test_horas_por_dia_densificadas_e_no_fuso_do_usuario(session, usuario):
    # 30 min reais - 5 de pausa - 60 s fora = 24 min de foco efetivo, no dia 10
    sessao(session, usuario, brt(2026, 9, 10, 20), minutos=30, pausas_min=5, fora_s=60)
    # 23:30 em São Paulo é 02:30 UTC do dia 12: conta no dia 11 (o dia do usuário)
    sessao(session, usuario, brt(2026, 9, 11, 23, 30), minutos=25)
    # Em andamento não entra
    sessao(session, usuario, brt(2026, 9, 11, 10), status="em_andamento")

    dados = _horas(session, usuario)
    assert len(dados) == 30  # todos os dias de setembro, mesmo os vazios
    por_dia = {d["periodo"]: d for d in dados}
    assert por_dia[date(2026, 9, 10)]["foco_s"] == 24 * 60
    assert por_dia[date(2026, 9, 11)] == {"periodo": date(2026, 9, 11), "foco_s": 25 * 60,
                                         "sessoes": 1, "concluidas": 1}
    assert por_dia[date(2026, 9, 12)]["sessoes"] == 0
    assert por_dia[date(2026, 9, 1)]["foco_s"] == 0


def test_horas_por_semana_comecam_na_segunda(session, usuario):
    sessao(session, usuario, brt(2026, 9, 9, 20), minutos=25)   # quarta 09/09
    sessao(session, usuario, brt(2026, 9, 13, 20), minutos=25)  # domingo 13/09, mesma semana
    sessao(session, usuario, brt(2026, 9, 14, 20), minutos=25)  # segunda 14/09, semana seguinte
    dados = {d["periodo"]: d["foco_s"] for d in _horas(session, usuario, "week")}
    assert dados[date(2026, 9, 7)] == 50 * 60
    assert dados[date(2026, 9, 14)] == 25 * 60
    assert min(dados) == date(2026, 8, 31)  # a semana de 01/09 começa na segunda 31/08


def test_horas_filtradas_por_disciplina(session, usuario, disciplina):
    sessao(session, usuario, brt(2026, 9, 10), disciplina_id=disciplina.id)
    sessao(session, usuario, brt(2026, 9, 10, 21))  # sem disciplina
    dados = _horas(session, usuario, d=disciplina.id)
    assert sum(d["sessoes"] for d in dados) == 1


def test_horas_de_outro_usuario_nao_aparecem(session, usuario, outro_usuario):
    sessao(session, outro_usuario, brt(2026, 9, 10))
    assert sum(d["sessoes"] for d in _horas(session, usuario)) == 0


# ---------------------------------------------- 2. concluídas × abandonadas


def test_concluidas_e_abandonadas_por_metodo_com_total(session, usuario):
    sessao(session, usuario, brt(2026, 9, 2), status="concluida")
    sessao(session, usuario, brt(2026, 9, 3), status="concluida")
    sessao(session, usuario, brt(2026, 9, 4), status="abandonada", minutos=10)
    sessao(session, usuario, brt(2026, 9, 5), metodo="52_17", status="abandonada", minutos=20)

    dados = analytics_foco.sessoes_por_metodo(session, usuario_id=usuario.id, disciplina_id=None,
                                              de=DE, ate=ATE)
    assert [d["metodo"] for d in dados] == ["pomodoro", "bloco", "52_17", "personalizado", "todos"]
    pomodoro, bloco, m52, _, todos = dados
    assert (pomodoro["concluidas"], pomodoro["abandonadas"]) == (2, 1)
    assert pomodoro["taxa_conclusao"] == Decimal("0.6667")
    assert (bloco["sessoes"], bloco["taxa_conclusao"]) == (0, None)  # método sem uso: zero, não some
    assert (m52["abandonadas"], m52["taxa_conclusao"]) == (1, Decimal("0"))
    assert (todos["sessoes"], todos["concluidas"]) == (4, 2)  # a linha do ROLLUP


# -------------------------------------------------------- 3. interrupções


def test_interrupcoes_por_sessao_e_por_hora(session, usuario):
    sessao(session, usuario, brt(2026, 9, 2), minutos=60, fora_s=120, programas=1)  # 2 interrupções
    sessao(session, usuario, brt(2026, 9, 3), minutos=25)
    dados = analytics_foco.interrupcoes(session, usuario_id=usuario.id, disciplina_id=None,
                                        de=DE, ate=ATE, limite=40)
    assert [d["interrupcoes"] for d in dados] == [2, 0]  # em ordem cronológica
    primeira = dados[0]
    assert (primeira["fora_s"], primeira["foco_efetivo_s"]) == (120, 58 * 60)
    assert primeira["por_hora"] == Decimal("2.07")  # 2 interrupções em 58 min


def test_interrupcoes_limita_as_mais_recentes(session, usuario):
    for dia in range(1, 6):
        sessao(session, usuario, brt(2026, 9, dia))
    dados = analytics_foco.interrupcoes(session, usuario_id=usuario.id, disciplina_id=None,
                                        de=DE, ate=ATE, limite=2)
    assert [d["dia"] for d in dados] == [date(2026, 9, 4), date(2026, 9, 5)]


# ---------------------------------------------- 4. acerto logo depois da sessão


def test_acerto_pos_sessao_classifica_cada_resposta(session, usuario, disciplina):
    c = card(session, disciplina)
    q, certa, errada = questao(session, disciplina)
    # Pomodoro termina 20:25; 52/17 termina no dia seguinte às 20:52
    sessao(session, usuario, brt(2026, 9, 10, 20), minutos=25)
    sessao(session, usuario, brt(2026, 9, 11, 20), minutos=52, metodo="52_17")

    revisar(session, c.id, brt(2026, 9, 10, 20, 30), nota=5)    # 5 min depois do pomodoro: acerto
    responder(session, q, errada, brt(2026, 9, 10, 21, 20))     # 55 min depois: erro
    revisar(session, c.id, brt(2026, 9, 10, 21, 30), nota=4)    # 65 min depois: fora da janela
    revisar(session, c.id, brt(2026, 9, 11, 21, 0), nota=2)     # 8 min depois do 52/17: erro
    responder(session, q, certa, brt(2026, 9, 11, 20, 30))      # DURANTE a 52/17: sem sessão antes

    dados = {d["grupo"]: d for d in analytics_foco.acerto_pos_sessao(
        session, usuario_id=usuario.id, disciplina_id=None, de=DE, ate=ATE, janela_min=60)}
    assert (dados["pomodoro"]["respostas"], dados["pomodoro"]["acertos"]) == (2, 1)
    assert dados["pomodoro"]["taxa"] == Decimal("0.5000")
    assert (dados["52_17"]["respostas"], dados["52_17"]["taxa"]) == (1, Decimal("0"))
    assert (dados["sem_sessao"]["respostas"], dados["sem_sessao"]["acertos"]) == (2, 2)
    assert (dados["bloco"]["respostas"], dados["bloco"]["taxa"]) == (0, None)


def test_acerto_pos_sessao_usa_a_ultima_sessao_antes(session, usuario, disciplina):
    c = card(session, disciplina)
    sessao(session, usuario, brt(2026, 9, 10, 19), minutos=25)                  # termina 19:25
    sessao(session, usuario, brt(2026, 9, 10, 19, 30), minutos=25, metodo="bloco")  # termina 19:55
    revisar(session, c.id, brt(2026, 9, 10, 20, 0))
    dados = {d["grupo"]: d["respostas"] for d in analytics_foco.acerto_pos_sessao(
        session, usuario_id=usuario.id, disciplina_id=None, de=DE, ate=ATE, janela_min=60)}
    assert (dados["bloco"], dados["pomodoro"]) == (1, 0)


def test_acerto_pos_sessao_ignora_sessoes_de_outro_usuario(session, usuario, outro_usuario, disciplina):
    c = card(session, disciplina)
    sessao(session, outro_usuario, brt(2026, 9, 10, 20), minutos=25)
    revisar(session, c.id, brt(2026, 9, 10, 20, 30))
    dados = {d["grupo"]: d["respostas"] for d in analytics_foco.acerto_pos_sessao(
        session, usuario_id=usuario.id, disciplina_id=None, de=DE, ate=ATE, janela_min=60)}
    assert (dados["pomodoro"], dados["sem_sessao"]) == (0, 1)


def test_lateral_usa_o_indice_e_nao_visita_a_tabela(session, usuario, disciplina):
    """O plano da subconsulta LATERAL: Index Only Scan no índice novo (com o método no
    INCLUDE). enable_seqscan=off só para a tabela minúscula do teste não ganhar no custo."""
    c = card(session, disciplina)
    sessao(session, usuario, brt(2026, 9, 10, 20))
    revisar(session, c.id, brt(2026, 9, 10, 20, 30))
    session.execute(text("SET LOCAL enable_seqscan = off"))
    sql = analytics_foco.SQL_ACERTO_POS_SESSAO.format(periodo="", filtro="")
    plano = "\n".join(r[0] for r in session.execute(
        text("EXPLAIN " + sql), {"usuario_id": usuario.id, "janela_min": 60}))
    assert "Index Only Scan Backward using ix_sessoes_estudo_usuario_terminada_em" in plano


# ----------------------------------------------------------------- pela API


@pytest.mark.parametrize("rota", ["horas", "horas?agrupar=semana", "sessoes", "interrupcoes",
                                  "acerto-pos-sessao"])
def test_rotas_respondem(client, headers, rota):
    resposta = client.get(f"/analytics/foco/{rota}", headers=headers)
    assert resposta.status_code == 200, resposta.text
    assert "dados" in resposta.json()


def test_janela_fora_dos_limites_da_422(client, headers):
    assert client.get("/analytics/foco/acerto-pos-sessao?janela_min=1", headers=headers).status_code == 422
