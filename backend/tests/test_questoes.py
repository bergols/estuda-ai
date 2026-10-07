from sqlalchemy import func, select

from app.models import Alternativa, Geracao, Questao, Tentativa
from tests.conftest import criar_pdf

TEXTO = "O índice padrão do Postgres é o B-tree, uma árvore balanceada com as chaves ordenadas."


def questao(enunciado="Qual é o índice padrão do Postgres?", correta="B", trechos=("T1",)):
    return {
        "enunciado": enunciado,
        "alternativas": ["Hash", "B-tree", "GIN", "BRIN"],
        "correta": correta,
        "explicacao": "O B-tree é o padrão; Hash só atende igualdade.",
        "dificuldade": 2,
        "topico": "Índices",
        "trechos": list(trechos),
    }


def preparar(client, headers, disciplina_id, anthropic_falso, *questoes):
    material_id = client.post(
        f"/disciplinas/{disciplina_id}/materiais",
        files={"arquivo": ("aula.pdf", criar_pdf([TEXTO]), "application/pdf")},
        headers=headers,
    ).json()["id"]
    anthropic_falso.roteiro.append({"questoes": list(questoes) or [questao()]})
    return client.post(
        f"/disciplinas/{disciplina_id}/questoes/gerar",
        json={"material_id": material_id, "quantidade": 3},
        headers=headers,
    )


def responder(client, headers, disciplina_id, questao_id, letra, tempo_ms=4200):
    return client.post(
        f"/disciplinas/{disciplina_id}/questoes/{questao_id}/tentativas",
        json={"alternativa": letra, "tempo_ms": tempo_ms},
        headers=headers,
    )


def test_gera_questao_com_alternativas_em_tabela_e_sem_gabarito_na_resposta(
    client, headers, disciplina_id, anthropic_falso, session
):
    resposta = preparar(client, headers, disciplina_id, anthropic_falso)

    assert resposta.status_code == 201
    q = resposta.json()["questoes"][0]
    assert [a["letra"] for a in q["alternativas"]] == ["A", "B", "C", "D"]
    assert all("correta" not in a for a in q["alternativas"])  # gabarito não vaza
    assert q["trechos"][0]["pagina"] == 1

    corretas = session.scalars(
        select(Alternativa.letra).where(Alternativa.questao_id == q["id"], Alternativa.correta)
    ).all()
    assert corretas == ["B"]
    questao_db = session.get(Questao, q["id"])
    assert questao_db.resposta_correta is None  # o gabarito mora em alternativas
    assert session.get(Geracao, questao_db.geracao_id).tipo == "questoes"


def test_alternativas_repetidas_geram_nova_tentativa(client, headers, disciplina_id, anthropic_falso):
    repetida = questao() | {"alternativas": ["B-tree", "b-tree ", "GIN", "BRIN"], "correta": "A"}
    anthropic_falso.roteiro.append({"questoes": [repetida]})

    resposta = preparar(client, headers, disciplina_id, anthropic_falso)

    assert resposta.status_code == 201
    assert resposta.json()["geracao"]["chamadas"] == 2
    assert "repetidas" in anthropic_falso.chamadas[1]["messages"][2]["content"]


def test_numero_errado_de_alternativas_e_rejeitado(client, headers, disciplina_id, anthropic_falso):
    tres = questao() | {"alternativas": ["Hash", "B-tree", "GIN"]}
    anthropic_falso.roteiro.append({"questoes": [tres]})

    resposta = preparar(client, headers, disciplina_id, anthropic_falso, tres)

    assert resposta.status_code == 502
    assert resposta.json()["detail"]["status"] == "erro_validacao"


def test_registra_tentativa_certa_com_tempo(client, headers, disciplina_id, anthropic_falso, session):
    q = preparar(client, headers, disciplina_id, anthropic_falso).json()["questoes"][0]

    resposta = responder(client, headers, disciplina_id, q["id"], "B")

    assert resposta.status_code == 201
    corpo = resposta.json()
    assert corpo["correta"] is True and corpo["alternativa_correta"] == "B"
    tentativa = session.get(Tentativa, corpo["tentativa_id"])
    assert tentativa.tempo_ms == 4200
    assert session.get(Alternativa, tentativa.alternativa_id).letra == "B"


def test_registra_tentativa_errada_e_devolve_gabarito(client, headers, disciplina_id, anthropic_falso):
    q = preparar(client, headers, disciplina_id, anthropic_falso).json()["questoes"][0]

    corpo = responder(client, headers, disciplina_id, q["id"], "A").json()

    assert corpo["correta"] is False
    assert corpo["alternativa_escolhida"] == "A" and corpo["alternativa_correta"] == "B"
    assert "B-tree" in corpo["explicacao"]


def test_letra_inexistente_retorna_422_e_nao_grava(client, headers, disciplina_id, anthropic_falso, session):
    q = preparar(client, headers, disciplina_id, anthropic_falso).json()["questoes"][0]

    assert responder(client, headers, disciplina_id, q["id"], "E").status_code == 422
    assert session.scalar(select(func.count()).select_from(Tentativa)) == 0


def test_questao_de_outra_disciplina_retorna_404(client, headers, disciplina_id, anthropic_falso):
    q = preparar(client, headers, disciplina_id, anthropic_falso).json()["questoes"][0]
    outra = client.post("/disciplinas", json={"nome": "Outra"}, headers=headers).json()["id"]

    assert responder(client, headers, outra, q["id"], "B").status_code == 404


def test_listar_questoes(client, headers, disciplina_id, anthropic_falso):
    preparar(client, headers, disciplina_id, anthropic_falso, questao(), questao("Para que serve o B-tree?"))

    questoes = client.get(f"/disciplinas/{disciplina_id}/questoes", headers=headers).json()

    assert len(questoes) == 2
    assert all(len(q["alternativas"]) == 4 for q in questoes)
