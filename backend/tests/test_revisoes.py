from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

import app.servicos.revisao as revisao
from app.models import Disciplina, Flashcard, HistoricoRevisao, Revisao, Usuario

BRT = timedelta(hours=-3)  # America/Sao_Paulo sem horário de verão (desde 2019)


def criar_card(session, disciplina_id, frente="O que é MVCC?", vence_em=None) -> Flashcard:
    card = Flashcard(disciplina_id=disciplina_id, frente=frente, verso="Versões de linha.")
    session.add(card)
    session.flush()
    if vence_em is not None:
        session.execute(
            text("UPDATE revisoes SET proxima_revisao = :v WHERE flashcard_id = :id"),
            {"v": vence_em, "id": card.id},
        )
    return card


def agora_brt(*args) -> datetime:
    """Um horário de parede em São Paulo, como instante UTC."""
    return datetime(*args, tzinfo=UTC) - BRT


@pytest.fixture
def disciplina(session, usuario):
    d = Disciplina(usuario_id=usuario.id, nome="Banco de Dados")
    session.add(d)
    session.flush()
    return d


# ----------------------------------------------------------------- fila do dia


def test_fila_traz_os_vencidos_do_mais_atrasado_para_o_menos(client, headers, session, disciplina):
    agora = datetime.now(UTC)
    a = criar_card(session, disciplina.id, "A", vence_em=agora - timedelta(days=3))
    b = criar_card(session, disciplina.id, "B", vence_em=agora - timedelta(days=10))
    criar_card(session, disciplina.id, "futuro", vence_em=agora + timedelta(days=5))

    fila = client.get("/revisoes/hoje", headers=headers).json()

    assert [c["flashcard_id"] for c in fila["cards"]] == [b.id, a.id]
    assert Decimal(fila["cards"][0]["atraso_dias"]) == pytest.approx(Decimal(10), abs=Decimal("0.01"))
    assert fila["cards"][0]["versao"] == 0
    assert fila["fuso_horario"] == "America/Sao_Paulo"


def test_fila_respeita_limite_e_filtro_de_disciplina(client, headers, session, usuario, disciplina):
    outra = Disciplina(usuario_id=usuario.id, nome="Redes")
    session.add(outra)
    session.flush()
    passado = datetime.now(UTC) - timedelta(days=1)
    for i in range(3):
        criar_card(session, disciplina.id, f"BD {i}", vence_em=passado)
    criar_card(session, outra.id, "Redes 1", vence_em=passado)

    so_bd = client.get(f"/revisoes/hoje?disciplina_id={disciplina.id}", headers=headers).json()
    dois = client.get("/revisoes/hoje?limite=2", headers=headers).json()

    assert {c["disciplina_nome"] for c in so_bd["cards"]} == {"Banco de Dados"}
    assert len(so_bd["cards"]) == 3
    assert len(dois["cards"]) == 2


def test_fila_nao_mostra_cards_de_outro_usuario(client, headers, session, outro_usuario):
    alheia = Disciplina(usuario_id=outro_usuario.id, nome="Alheia")
    session.add(alheia)
    session.flush()
    criar_card(session, alheia.id, vence_em=datetime.now(UTC) - timedelta(days=1))

    assert client.get("/revisoes/hoje", headers=headers).json()["cards"] == []
    assert client.get(f"/revisoes/hoje?disciplina_id={alheia.id}", headers=headers).status_code == 404


def test_card_novo_ja_aparece_na_fila(client, headers, session, disciplina):
    card = criar_card(session, disciplina.id)  # proxima_revisao = now() pelo DEFAULT

    fila = client.get("/revisoes/hoje", headers=headers).json()

    assert [c["flashcard_id"] for c in fila["cards"]] == [card.id]


# ----------------------------------------------------------------- fuso horário


def test_hoje_e_o_dia_do_usuario_e_nao_o_dia_em_utc(session, usuario, disciplina):
    """22h30 de 07/10 em São Paulo = 01h30 de 08/10 em UTC.

    Em UTC "hoje" já seria 08/10 e o card que vence às 10h de 08/10 (horário de
    São Paulo) entraria na fila uma noite antes. No fuso do usuário, não entra."""
    agora = agora_brt(2026, 10, 7, 22, 30)
    hoje_ainda = criar_card(session, disciplina.id, "hoje", vence_em=agora_brt(2026, 10, 7, 23, 50))
    amanha = criar_card(session, disciplina.id, "amanhã", vence_em=agora_brt(2026, 10, 8, 10, 0))

    fila = revisao.fila_do_dia(
        session, usuario_id=usuario.id, disciplina_id=None, limite=10, agora=agora
    )

    assert [c["flashcard_id"] for c in fila.cards] == [hoje_ainda.id]
    assert amanha.id not in [c["flashcard_id"] for c in fila.cards]
    assert fila.fim_de_hoje == agora_brt(2026, 10, 8, 0, 0)  # meia-noite em São Paulo


def test_o_mesmo_instante_e_outro_dia_em_outro_fuso(session, usuario, disciplina):
    agora = agora_brt(2026, 10, 7, 22, 30)  # já é 08/10 10h30 em Tóquio
    criar_card(session, disciplina.id, vence_em=agora_brt(2026, 10, 8, 10, 0))
    session.execute(
        text("UPDATE usuarios SET fuso_horario = 'Asia/Tokyo' WHERE id = :id"), {"id": usuario.id}
    )

    fila = revisao.fila_do_dia(
        session, usuario_id=usuario.id, disciplina_id=None, limite=10, agora=agora
    )

    assert len(fila.cards) == 1  # em Tóquio esse vencimento já é "hoje"
    assert fila.fim_de_hoje == datetime(2026, 10, 8, 15, 0, tzinfo=UTC)  # 09/10 00h em Tóquio


# ------------------------------------------------------------ registrar revisão


def revisar(client, headers, card_id, nota, versao=0):
    return client.post(f"/revisoes/{card_id}", json={"nota": nota, "versao": versao}, headers=headers)


def test_revisao_atualiza_estado_e_grava_historico(client, headers, session, disciplina):
    card = criar_card(session, disciplina.id)

    resposta = revisar(client, headers, card.id, 5)

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["anterior"] == {"facilidade": "2.50", "intervalo_dias": 0, "repeticoes": 0}
    assert corpo["novo"] == {"facilidade": "2.60", "intervalo_dias": 1, "repeticoes": 1}
    assert corpo["versao"] == 1

    estado = session.get(Revisao, card.id)
    session.refresh(estado)
    assert (estado.facilidade, estado.intervalo_dias, estado.versao) == (Decimal("2.60"), 1, 1)
    h = session.get(HistoricoRevisao, corpo["historico_id"])
    assert (h.nota, h.intervalo_anterior, h.intervalo_novo) == (5, 0, 1)
    assert h.proxima_revisao_nova - h.revisado_em == timedelta(days=1)


def test_tres_revisoes_seguidas_seguem_o_sm2(client, headers, session, disciplina):
    card = criar_card(session, disciplina.id)

    intervalos = [revisar(client, headers, card.id, 4, versao=v).json()["novo"]["intervalo_dias"]
                  for v in range(3)]

    assert intervalos == [1, 6, 15]
    total = session.scalar(
        select(func.count()).select_from(HistoricoRevisao).where(HistoricoRevisao.flashcard_id == card.id)
    )
    assert total == 3


def test_versao_desatualizada_retorna_409_e_nao_grava_nada(client, headers, session, disciplina):
    card = criar_card(session, disciplina.id)
    revisar(client, headers, card.id, 4, versao=0)

    resposta = revisar(client, headers, card.id, 4, versao=0)  # segunda aba, versão velha

    assert resposta.status_code == 409
    assert resposta.json()["detail"]["versao_atual"] == 1
    assert session.scalar(select(func.count()).select_from(HistoricoRevisao)) == 1


@pytest.mark.parametrize("nota", [-1, 6])
def test_nota_fora_de_0_a_5_retorna_422(client, headers, session, disciplina, nota):
    card = criar_card(session, disciplina.id)
    assert revisar(client, headers, card.id, nota).status_code == 422


def test_card_de_outro_usuario_retorna_404(client, headers, session, outro_usuario):
    alheia = Disciplina(usuario_id=outro_usuario.id, nome="Alheia")
    session.add(alheia)
    session.flush()
    card = criar_card(session, alheia.id)

    assert revisar(client, headers, card.id, 5).status_code == 404


def test_falha_no_historico_desfaz_o_update_do_estado(session, usuario, disciplina, monkeypatch):
    """Estado e histórico na MESMA transação: se o INSERT do histórico falha,
    o UPDATE do estado (que já tinha rodado) é desfeito pelo ROLLBACK."""
    card = criar_card(session, disciplina.id)
    card_id = card.id
    # Na vida real o card já existe (commitado) antes da revisão. Sem este commit,
    # o ROLLBACK da revisão desfaria também a criação do card feita neste teste.
    session.commit()

    def falhar(*args, **kwargs):
        raise RuntimeError("falha simulada depois do UPDATE")

    monkeypatch.setattr(revisao, "_inserir_historico", falhar)

    with pytest.raises(RuntimeError):
        revisao.registrar_revisao(
            session, usuario_id=usuario.id, flashcard_id=card_id, nota=5, versao=0
        )

    estado = session.execute(
        text("SELECT versao, repeticoes FROM revisoes WHERE flashcard_id = :id"), {"id": card_id}
    ).one()
    assert tuple(estado) == (0, 0)  # o UPDATE não sobreviveu
    assert session.scalar(select(func.count()).select_from(HistoricoRevisao)) == 0


def test_usuario_lido_do_banco_tem_fuso_padrao(session, usuario):
    session.refresh(usuario)
    assert session.get(Usuario, usuario.id).fuso_horario == "America/Sao_Paulo"
