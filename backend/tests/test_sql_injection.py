"""SQL injection: entradas maliciosas viram DADO, nunca SQL.

Toda consulta usa parâmetros (bind parameters): o texto do SQL e os valores vão
separados para o Postgres, que nunca interpreta um valor como comando. Estes testes
mandam payloads clássicos em toda entrada de texto da API e conferem que (1) nada
quebra, (2) o payload foi tratado como texto comum e (3) nenhuma tabela foi
apagada. Explicação com exemplo em docs/seguranca.md.
"""

from datetime import date

import pytest
from sqlalchemy import text

from app.servicos import analytics
from tests.conftest import criar_pdf

PAYLOADS = [
    "'; DROP TABLE disciplinas; --",
    "' OR '1'='1",
    "x' UNION SELECT senha_hash FROM usuarios --",
    "1; UPDATE usuarios SET versao_token = 99; --",
    "$$; DROP TABLE flashcards; $$",
]


def tabelas_intactas(session):
    for tabela in ("disciplinas", "flashcards", "usuarios", "materiais"):
        session.execute(text(f"SELECT 1 FROM {tabela} LIMIT 1"))  # erraria se tivesse sumido
    return session.scalar(text("SELECT max(versao_token) FROM usuarios")) == 0


@pytest.mark.parametrize("payload", PAYLOADS)
def test_nome_de_disciplina_malicioso_e_gravado_como_texto(client, headers, session, payload):
    resposta = client.post("/disciplinas", json={"nome": payload}, headers=headers)

    assert resposta.status_code == 201
    assert resposta.json()["nome"] == payload.strip()  # o "comando" virou só um nome
    assert tabelas_intactas(session)


@pytest.mark.parametrize("payload", PAYLOADS)
def test_busca_textual_com_payload(client, headers, disciplina_id, session, payload):
    client.post(
        f"/disciplinas/{disciplina_id}/materiais",
        files={"arquivo": ("a.pdf", criar_pdf(["Índices B-tree"]), "application/pdf")},
        headers=headers,
    )
    for modo in ("textual", "semantica", "hibrida"):
        resposta = client.get(
            f"/disciplinas/{disciplina_id}/busca", params={"q": payload, "modo": modo},
            headers=headers,
        )
        assert resposta.status_code == 200, (modo, resposta.text)
    assert tabelas_intactas(session)


@pytest.mark.parametrize("payload", PAYLOADS)
def test_login_com_payload_nao_entra(client, usuario, session, payload):
    resposta = client.post("/auth/login", data={"username": payload, "password": payload})

    assert resposta.status_code == 401  # "' OR '1'='1" não vira "qualquer usuário"
    assert tabelas_intactas(session)


@pytest.mark.parametrize("valor", ["2026-01-01'; DROP TABLE disciplinas; --", "1 OR 1=1"])
def test_parametros_tipados_recusam_payload(client, headers, valor):
    """Datas e ids são validados ANTES de chegar ao banco: o payload nem vira SQL."""
    assert client.get(f"/analytics/calendario?de={valor}", headers=headers).status_code == 422
    assert client.get(f"/analytics/sequencia?disciplina_id={valor}", headers=headers).status_code == 422


def test_filtros_dinamicos_so_aceitam_nomes_de_coluna():
    """_filtro/_periodo são os únicos pontos que montam texto SQL em tempo de execução."""
    assert analytics._filtro("m.disciplina_id", 1) == "AND m.disciplina_id = :disciplina_id"
    periodo = analytics._periodo("h.revisado_em", date(2026, 1, 1), date(2026, 2, 1))
    assert "2026" not in periodo and ":de" in periodo and ":ate" in periodo  # valores não entram
    for ruim in ("x; DROP TABLE y", "a = 1 OR 1", "col --", "Col"):
        with pytest.raises(ValueError):
            analytics._filtro(ruim, 1)
        with pytest.raises(ValueError):
            analytics._periodo(ruim, None, None)
