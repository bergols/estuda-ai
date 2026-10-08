from datetime import datetime
from decimal import Decimal

from sqlalchemy import text

from app.models import Disciplina, Geracao


def gerar_linha(session, usuario, disciplina_id, quando, custo, tokens=(100, 10), status="sucesso"):
    g = Geracao(
        usuario_id=usuario.id, disciplina_id=disciplina_id, tipo="pergunta",
        modelo="claude-haiku-4-5-20251001", tokens_entrada=tokens[0], tokens_saida=tokens[1],
        custo_usd=custo, duracao_ms=10, status=status,
        erro_mensagem=None if status == "sucesso" else "falhou",
        # criado_em já no INSERT: geracoes é só-INSERT e o papel da API não tem UPDATE nela
        criado_em=datetime.fromisoformat(quando),
    )
    session.add(g)
    session.flush()


def test_gasto_por_disciplina_e_mes(client, headers, usuario, session):
    bd = Disciplina(usuario_id=usuario.id, nome="BD")
    redes = Disciplina(usuario_id=usuario.id, nome="Redes")
    session.add_all([bd, redes])
    session.flush()
    gerar_linha(session, usuario, bd.id, "2026-09-10 12:00-03", Decimal("0.010"))
    gerar_linha(session, usuario, bd.id, "2026-09-20 12:00-03", Decimal("0.005"), status="erro_validacao")
    gerar_linha(session, usuario, bd.id, "2026-10-05 12:00-03", Decimal("0.002"))
    gerar_linha(session, usuario, redes.id, "2026-10-06 12:00-03", Decimal("0.004"))

    linhas = client.get("/gastos", headers=headers).json()

    resumo = [(item["disciplina_nome"], item["mes"], item["geracoes"], Decimal(item["custo_usd"])) for item in linhas]
    assert resumo == [
        ("Redes", "2026-10-01", 1, Decimal("0.004")),
        ("BD", "2026-10-01", 1, Decimal("0.002")),
        ("BD", "2026-09-01", 2, Decimal("0.015")),  # a falha também foi cobrada
    ]


def test_mes_e_calculado_no_fuso_de_sao_paulo(client, headers, usuario, session):
    d = Disciplina(usuario_id=usuario.id, nome="BD")
    session.add(d)
    session.flush()
    # 23h30 de 31/10 em Brasília = 02h30 de 01/11 em UTC
    gerar_linha(session, usuario, d.id, "2026-10-31 23:30-03", Decimal("0.001"))

    [linha] = client.get("/gastos", headers=headers).json()

    assert linha["mes"] == "2026-10-01"


def test_disciplina_apagada_continua_no_relatorio(client, headers, usuario, session):
    d = Disciplina(usuario_id=usuario.id, nome="Temporária")
    session.add(d)
    session.flush()
    gerar_linha(session, usuario, d.id, "2026-10-05 12:00-03", Decimal("0.003"))
    session.execute(text("DELETE FROM disciplinas WHERE id = :id"), {"id": d.id})

    [linha] = client.get("/gastos", headers=headers).json()

    assert linha["disciplina_id"] is None and linha["disciplina_nome"] is None
    assert Decimal(linha["custo_usd"]) == Decimal("0.003")


def test_custo_desconhecido_soma_como_zero_e_so_mostra_o_proprio_usuario(
    client, headers, usuario, outro_usuario, session
):
    minha = Disciplina(usuario_id=usuario.id, nome="Minha")
    alheia = Disciplina(usuario_id=outro_usuario.id, nome="Alheia")
    session.add_all([minha, alheia])
    session.flush()
    gerar_linha(session, usuario, minha.id, "2026-10-05 12:00-03", None)
    gerar_linha(session, outro_usuario, alheia.id, "2026-10-05 12:00-03", Decimal("9"))

    linhas = client.get("/gastos", headers=headers).json()

    assert [(item["disciplina_nome"], Decimal(item["custo_usd"])) for item in linhas] == [("Minha", Decimal("0"))]
