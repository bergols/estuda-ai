"""Rate limiting (UPSERT no Postgres) e cota diária de gerações de IA."""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Geracao, Usuario
from app.servicos import auth, limites

SENHA = "uma senha longa o bastante"


@pytest.fixture
def com_senha(session):
    u = Usuario(nome="Dona", email="dona@furg.br", senha_hash=auth.gerar_hash(SENHA))
    session.add(u)
    session.flush()
    return u


def login(client, email, senha):
    return client.post("/auth/login", data={"username": email, "password": senha})


# --------------------------------------------------------------- login


def test_cinco_senhas_erradas_bloqueiam_o_email_ate_a_senha_certa(client, com_senha):
    for _ in range(5):
        assert login(client, "dona@furg.br", "chute errado numero x").status_code == 401

    bloqueado = login(client, "dona@furg.br", SENHA)  # agora nem a senha certa entra

    assert bloqueado.status_code == 429
    assert 0 < int(bloqueado.headers["retry-after"]) <= 15 * 60


def test_logins_certos_nao_trancam_a_propria_conta(client, com_senha):
    for _ in range(8):  # mais que o limite de falhas por e-mail
        assert login(client, "dona@furg.br", SENHA).status_code == 200


def test_limite_por_ip_vale_para_qualquer_email(client, com_senha, settings_teste):
    settings_teste.limite_login_por_ip = 3
    for i in range(3):
        login(client, f"tentativa{i}@furg.br", "qualquer senha longa")

    assert login(client, "dona@furg.br", SENHA).status_code == 429


# ----------------------------------------------------------- rotas de IA


def perguntar(client, headers, disciplina_id):
    return client.post(f"/disciplinas/{disciplina_id}/perguntar",
                       json={"pergunta": "o que é MVCC?"}, headers=headers)


def test_rate_limit_por_minuto_nas_rotas_de_ia(client, headers, disciplina_id, settings_teste):
    settings_teste.limite_ia_por_minuto = 2
    # disciplina sem material: a rota responde sem chamar o LLM, mas conta no limite
    assert perguntar(client, headers, disciplina_id).status_code == 200
    assert perguntar(client, headers, disciplina_id).status_code == 200

    resposta = perguntar(client, headers, disciplina_id)

    assert resposta.status_code == 429 and "retry-after" in resposta.headers


def gerar(session, usuario, disciplina_id, quando: datetime):
    g = Geracao(usuario_id=usuario.id, disciplina_id=disciplina_id, tipo="pergunta",
                modelo="claude-haiku-4-5-20251001", custo_usd=Decimal("0.01"),
                duracao_ms=1, status="sucesso")
    session.add(g)
    session.flush()
    session.execute(text("UPDATE geracoes SET criado_em = :q WHERE id = :id"),
                    {"q": quando, "id": g.id})


def test_cota_diaria_bloqueia_antes_de_chamar_o_llm(client, headers, session, usuario,
                                                     disciplina_id, settings_teste, anthropic_falso):
    settings_teste.limite_geracoes_dia = 3
    agora = datetime.now(UTC)
    for _ in range(3):
        gerar(session, usuario, disciplina_id, agora - timedelta(minutes=1))

    resposta = perguntar(client, headers, disciplina_id)

    assert resposta.status_code == 429
    assert "limite diário" in resposta.json()["detail"]
    assert anthropic_falso.chamadas == []


def test_geracoes_de_ontem_nao_contam_na_cota_de_hoje(client, headers, session, usuario,
                                                       disciplina_id, settings_teste):
    settings_teste.limite_geracoes_dia = 3
    inicio_de_hoje = session.scalar(text(
        "SELECT date_trunc('day', now() AT TIME ZONE 'America/Sao_Paulo') AT TIME ZONE 'America/Sao_Paulo'"
    ))
    for _ in range(5):  # 1 minuto antes da meia-noite local
        gerar(session, usuario, disciplina_id, inicio_de_hoje - timedelta(minutes=1))

    assert perguntar(client, headers, disciplina_id).status_code == 200


def test_cota_zero_desliga_a_ia(client, headers, disciplina_id, settings_teste):
    settings_teste.limite_geracoes_dia = 0
    for rota, corpo in (("perguntar", {"pergunta": "x"}), ("flashcards/gerar", {"tema": "x"}),
                        ("questoes/gerar", {"tema": "x"})):
        resposta = client.post(f"/disciplinas/{disciplina_id}/{rota}", json=corpo, headers=headers)
        assert resposta.status_code == 429, rota


# ------------------------------------------------- atomicidade do UPSERT


def test_upsert_conta_certo_com_20_requisicoes_simultaneas(engine):
    """20 threads, cada uma com sua conexão, contam na MESMA chave ao mesmo tempo.
    Sem atomicidade (ler, somar, gravar) várias leriam o mesmo valor e o total
    ficaria abaixo de 20. Com o UPSERT, exatamente 20 e exatamente 5 passam."""
    chave = f"teste:{uuid.uuid4().hex}"
    largada = threading.Barrier(20, timeout=10)

    def requisicao():
        with Session(engine) as s:
            largada.wait()
            try:
                limites.consumir(s, chave, 5, timedelta(minutes=1))
                return "ok"
            except limites.Excedido:
                return "429"

    try:
        with ThreadPoolExecutor(20) as executor:
            resultados = list(executor.map(lambda _: requisicao(), range(20)))
        with Session(engine) as s:
            total = s.scalar(text("SELECT sum(contagem) FROM limites_taxa WHERE chave = :c"),
                             {"c": chave})
    finally:
        with Session(engine) as s:
            s.execute(text("DELETE FROM limites_taxa WHERE chave = :c"), {"c": chave})
            s.commit()

    assert total == 20
    assert resultados.count("ok") == 5 and resultados.count("429") == 15
