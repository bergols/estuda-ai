"""Testes da busca. Cada PDF aqui é curto e vira um único trecho, então "qual
material veio primeiro" equivale a "qual trecho veio primeiro"."""

import pytest
from sqlalchemy import select, text

from app.models import Trecho
from app.servicos.embeddings import EmbedderE5
from tests.conftest import cabecalho, criar_pdf

TEXTOS = {
    "indices": "Um índice B-tree mantém as chaves ordenadas e evita ler a tabela inteira.",
    "transacoes": "Uma transação garante atomicidade: o ROLLBACK desfaz todas as operações.",
    "normalizacao": "A terceira forma normal elimina dependências transitivas e redundância.",
}


# Parágrafos do tamanho de um trecho real. Com frases curtas (uma linha) o
# e5-small erra: as similaridades ficam todas entre 0,81 e 0,87 e a margem entre
# o certo e o errado cai para milésimos. Ver docs/busca-semantica.md.
TEXTOS_LONGOS = {
    "indices": (
        "Um índice B-tree mantém as chaves ordenadas em uma árvore balanceada. Com ele, o "
        "banco localiza uma linha descendo poucos níveis da árvore, sem precisar ler a tabela "
        "inteira. Índices aceleram consultas com WHERE e ORDER BY, mas deixam INSERT e UPDATE "
        "mais lentos, porque cada índice também precisa ser atualizado."
    ),
    "transacoes": (
        "Uma transação agrupa várias operações em uma unidade indivisível. Se todas derem "
        "certo, o COMMIT torna as alterações permanentes; se alguma falhar, o ROLLBACK desfaz "
        "tudo o que foi feito desde o BEGIN. Essa propriedade se chama atomicidade, o A de "
        "ACID, e garante que o banco nunca fique pela metade."
    ),
    "normalizacao": (
        "A normalização organiza as tabelas para eliminar redundância. Na terceira forma "
        "normal, nenhum atributo depende de outro atributo que não seja chave, ou seja, não há "
        "dependências transitivas. Guardar a mesma informação em dois lugares abre espaço para "
        "inconsistências quando só uma das cópias é atualizada."
    ),
}


@pytest.fixture
def materiais(client, headers, disciplina_id):
    ids = {}
    for nome, texto in TEXTOS.items():
        resposta = client.post(
            f"/disciplinas/{disciplina_id}/materiais",
            files={"arquivo": (f"{nome}.pdf", criar_pdf([texto]), "application/pdf")},
            headers=headers,
        )
        ids[nome] = resposta.json()["id"]
    return ids


def buscar(client, headers, disciplina_id, q, **params):
    return client.get(
        f"/disciplinas/{disciplina_id}/busca", params={"q": q, **params}, headers=headers
    )


# ---------------------------------------------------------------- semântica


def test_busca_semantica_ordena_por_similaridade(client, headers, disciplina_id, materiais):
    resposta = buscar(client, headers, disciplina_id, "ROLLBACK desfaz operações", k=3)

    assert resposta.status_code == 200
    resultados = resposta.json()
    assert resultados[0]["material_id"] == materiais["transacoes"]
    assert resultados[0]["material_titulo"] == "transacoes"
    assert resultados[0]["pagina"] == 1
    scores = [r["score"] for r in resultados]
    assert scores == sorted(scores, reverse=True)
    assert all(-1 <= s <= 1 for s in scores)  # similaridade de cosseno


def test_busca_respeita_k(client, headers, disciplina_id, materiais):
    assert len(buscar(client, headers, disciplina_id, "tabela", k=2).json()) == 2


def test_busca_so_olha_a_disciplina_pedida(client, headers, disciplina_id, materiais):
    outra = client.post("/disciplinas", json={"nome": "Outra"}, headers=headers).json()["id"]
    client.post(
        f"/disciplinas/{outra}/materiais",
        files={"arquivo": ("x.pdf", criar_pdf(["ROLLBACK desfaz operações"]), "application/pdf")},
        headers=headers,
    )

    resultados = buscar(client, headers, disciplina_id, "ROLLBACK desfaz operações", k=10).json()

    assert {r["material_id"] for r in resultados} == set(materiais.values())


def test_disciplina_sem_materiais_devolve_lista_vazia(client, headers, disciplina_id):
    assert buscar(client, headers, disciplina_id, "qualquer coisa").json() == []


def test_hnsw_ef_search_e_iterative_scan_valem_so_na_transacao(
    client, headers, disciplina_id, materiais, session
):
    buscar(client, headers, disciplina_id, "tabela", k=5)

    # set_config(..., true) = SET LOCAL: a configuração morre no fim da transação.
    # Aqui ainda estamos na transação do teste, então ela continua visível...
    assert session.scalar(text("SHOW hnsw.iterative_scan")) == "strict_order"
    session.rollback()
    # ...e some depois do rollback (a sessão do teste volta ao savepoint).
    assert session.scalar(text("SHOW hnsw.iterative_scan")) == "off"


# ------------------------------------------------------------------ textual


def test_busca_textual_ignora_acento_e_usa_radical(client, headers, disciplina_id, materiais):
    # "indice" (sem acento, singular) encontra "índice"; "ordenada" encontra "ordenadas"
    resultados = buscar(client, headers, disciplina_id, "indice ordenada", modo="textual").json()

    assert [r["material_id"] for r in resultados] == [materiais["indices"]]
    assert 0 < resultados[0]["score"] < 1  # ts_rank_cd normalizado (flag 32)


def test_busca_textual_exige_todas_as_palavras(client, headers, disciplina_id, materiais):
    # "índice" está num material e "rollback" em outro: nenhum tem as duas
    assert buscar(client, headers, disciplina_id, "índice rollback", modo="textual").json() == []


def test_busca_textual_aceita_or_e_frase(client, headers, disciplina_id, materiais):
    resultados = buscar(
        client, headers, disciplina_id, "índice OR rollback", modo="textual"
    ).json()
    assert {r["material_id"] for r in resultados} == {materiais["indices"], materiais["transacoes"]}

    frase = buscar(client, headers, disciplina_id, '"forma normal"', modo="textual").json()
    assert [r["material_id"] for r in frase] == [materiais["normalizacao"]]


# ------------------------------------------------------------------ híbrida


def test_busca_hibrida_combina_posicoes_com_rrf(client, headers, disciplina_id, materiais):
    resultados = buscar(
        client, headers, disciplina_id, "rollback desfaz operações", modo="hibrida", k=3
    ).json()

    primeiro = resultados[0]
    assert primeiro["material_id"] == materiais["transacoes"]
    assert primeiro["posicao_semantica"] == 1 and primeiro["posicao_textual"] == 1
    for r in resultados:
        esperado = sum(1 / (60 + p) for p in (r["posicao_semantica"], r["posicao_textual"]) if p)
        assert r["score"] == pytest.approx(esperado)


def test_busca_hibrida_usa_ou_na_parte_textual(client, headers, disciplina_id, materiais):
    # Em modo textual (E), "índice rollback" não acha nada; na híbrida a perna
    # textual usa OU, e os dois materiais aparecem com posição textual.
    resultados = buscar(
        client, headers, disciplina_id, "índice rollback", modo="hibrida", k=3
    ).json()

    com_textual = {r["material_id"] for r in resultados if r["posicao_textual"]}
    assert com_textual == {materiais["indices"], materiais["transacoes"]}


# --------------------------------------------------------------- validação


@pytest.mark.parametrize(
    "params",
    [{"q": ""}, {"q": "   "}, {"q": "x", "k": 0}, {"q": "x", "k": 51}, {"q": "x", "modo": "outro"}],
)
def test_parametros_invalidos_retornam_422(client, headers, disciplina_id, params):
    resposta = client.get(f"/disciplinas/{disciplina_id}/busca", params=params, headers=headers)
    assert resposta.status_code == 422


def test_busca_em_disciplina_de_outro_usuario_retorna_404(client, disciplina_id, outro_usuario):
    resposta = buscar(client, cabecalho(outro_usuario), disciplina_id, "x")
    assert resposta.status_code == 404


# ------------------------------------------------- com o modelo real (lento)


@pytest.mark.modelo
def test_modelo_real_encontra_por_significado_sem_palavras_em_comum(
    client, headers, disciplina_id, session, fabrica, pasta_uploads
):
    """Nenhuma palavra da pergunta aparece no texto certo: só embeddings acham."""
    real = EmbedderE5("intfloat/multilingual-e5-small")
    from app.main import app
    from app.servicos.embeddings import get_embedder

    app.dependency_overrides[get_embedder] = lambda: real
    ids = {}
    for nome, texto in TEXTOS_LONGOS.items():
        r = client.post(
            f"/disciplinas/{disciplina_id}/materiais",
            files={"arquivo": (f"{nome}.pdf", criar_pdf([texto]), "application/pdf")},
            headers=headers,
        )
        ids[nome] = r.json()["id"]
    assert session.scalar(select(Trecho.id).limit(1)) is not None

    perguntas = {
        "como voltar atrás quando algo dá errado no meio": "transacoes",
        "estrutura para achar registros rapidamente": "indices",
        "evitar dados repetidos no projeto do esquema": "normalizacao",
    }
    for pergunta, esperado in perguntas.items():
        resultados = buscar(client, headers, disciplina_id, pergunta, k=1).json()
        assert resultados[0]["material_id"] == ids[esperado], pergunta
