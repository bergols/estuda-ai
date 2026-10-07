from sqlalchemy import func, select, text

from app.models import Flashcard, Geracao, Material, Trecho
from app.servicos import gerar
from tests.conftest import criar_pdf

TEXTO = "Uma transação garante atomicidade: o ROLLBACK desfaz todas as operações feitas."


def card(frente, verso, trechos=("T1",), topico="Transações"):
    return {"frente": frente, "verso": verso, "topico": topico, "trechos": list(trechos)}


def enviar_material(client, headers, disciplina_id, texto=TEXTO, nome="aula.pdf"):
    return client.post(
        f"/disciplinas/{disciplina_id}/materiais",
        files={"arquivo": (nome, criar_pdf([texto]), "application/pdf")},
        headers=headers,
    ).json()["id"]


def gerar_cards(client, headers, disciplina_id, **corpo):
    return client.post(f"/disciplinas/{disciplina_id}/flashcards/gerar", json=corpo, headers=headers)


def test_gera_flashcards_ligados_aos_trechos_e_a_geracao(
    client, headers, disciplina_id, anthropic_falso, session
):
    material_id = enviar_material(client, headers, disciplina_id)
    anthropic_falso.roteiro.append({"flashcards": [
        card("O que o ROLLBACK faz?", "Desfaz as operações da transação."),
        card("O que é atomicidade?", "Tudo ou nada: ou todas as operações valem ou nenhuma."),
    ]})

    resposta = gerar_cards(client, headers, disciplina_id, material_id=material_id, quantidade=5)

    assert resposta.status_code == 201
    corpo = resposta.json()
    assert len(corpo["criados"]) == 2 and corpo["descartados"] == []
    trecho_id = session.scalar(select(Trecho.id).where(Trecho.material_id == material_id))
    assert corpo["criados"][0]["trechos"] == [{"id": trecho_id, "material_id": material_id, "pagina": 1}]

    geracao = session.get(Geracao, corpo["geracao"]["id"])
    assert geracao.tipo == "flashcards" and geracao.status == "sucesso"
    cards = session.scalars(select(Flashcard).where(Flashcard.geracao_id == geracao.id)).all()
    assert len(cards) == 2
    assert all(c.origem == "ia" and c.embedding is not None for c in cards)


def test_descarta_card_parecido_com_um_existente(
    client, headers, disciplina_id, anthropic_falso, session
):
    material_id = enviar_material(client, headers, disciplina_id)
    original = card("O que o ROLLBACK faz?", "Desfaz as operações da transação.")
    anthropic_falso.roteiro.append({"flashcards": [original]})
    primeiro = gerar_cards(client, headers, disciplina_id, material_id=material_id).json()
    anthropic_falso.roteiro.append({"flashcards": [
        original,  # o LLM repetiu o mesmo card
        card("O que é atomicidade?", "Tudo ou nada: ou todas as operações valem ou nenhuma."),
    ]})

    corpo = gerar_cards(client, headers, disciplina_id, material_id=material_id).json()

    assert [c["frente"] for c in corpo["criados"]] == ["O que é atomicidade?"]
    descartado = corpo["descartados"][0]
    assert descartado["parecido_com_id"] == primeiro["criados"][0]["id"]
    assert descartado["similaridade"] >= gerar.LIMIAR_DUPLICATA
    assert corpo["limiar_duplicata"] == gerar.LIMIAR_DUPLICATA
    total = session.scalar(select(func.count()).where(Flashcard.disciplina_id == disciplina_id))
    assert total == 2


def test_descarta_duplicata_dentro_do_mesmo_lote(client, headers, disciplina_id, anthropic_falso):
    """A transação enxerga as próprias escritas ainda não commitadas."""
    material_id = enviar_material(client, headers, disciplina_id)
    repetido = card("O que o ROLLBACK faz?", "Desfaz as operações da transação.")
    anthropic_falso.roteiro.append({"flashcards": [repetido, repetido]})

    corpo = gerar_cards(client, headers, disciplina_id, material_id=material_id).json()

    assert len(corpo["criados"]) == 1 and len(corpo["descartados"]) == 1
    assert corpo["descartados"][0]["parecido_com_id"] == corpo["criados"][0]["id"]


def test_card_de_conceito_diferente_nao_e_descartado(
    client, headers, disciplina_id, anthropic_falso
):
    material_id = enviar_material(client, headers, disciplina_id)
    anthropic_falso.roteiro.append({"flashcards": [
        card("Para que serve o COMMIT?", "Torna permanentes as alterações da transação."),
        card("Para que serve o ROLLBACK?", "Desfaz as alterações da transação atual."),
    ]})

    corpo = gerar_cards(client, headers, disciplina_id, material_id=material_id).json()

    assert len(corpo["criados"]) == 2


def test_prompt_lista_os_cards_ja_existentes(client, headers, disciplina_id, anthropic_falso):
    material_id = enviar_material(client, headers, disciplina_id)
    anthropic_falso.roteiro.append({"flashcards": [card("O que o ROLLBACK faz?", "Desfaz.")]})
    gerar_cards(client, headers, disciplina_id, material_id=material_id)
    anthropic_falso.roteiro.append({"flashcards": [card("O que é atomicidade?", "Tudo ou nada.")]})

    gerar_cards(client, headers, disciplina_id, material_id=material_id)

    prompt = anthropic_falso.chamadas[1]["messages"][0]["content"]
    assert "<ja_existentes>\n- O que o ROLLBACK faz?" in prompt


def test_gera_a_partir_de_um_tema(client, headers, disciplina_id, anthropic_falso):
    enviar_material(client, headers, disciplina_id)
    anthropic_falso.roteiro.append({"flashcards": [card("O que o ROLLBACK faz?", "Desfaz.")]})

    resposta = gerar_cards(client, headers, disciplina_id, tema="rollback")

    assert resposta.status_code == 201
    assert "ROLLBACK desfaz" in anthropic_falso.chamadas[0]["messages"][0]["content"]


def test_mais_cards_que_o_pedido_gera_nova_tentativa(client, headers, disciplina_id, anthropic_falso):
    material_id = enviar_material(client, headers, disciplina_id)
    anthropic_falso.roteiro += [
        {"flashcards": [card(f"Pergunta {i}?", "Resposta.") for i in range(3)]},
        {"flashcards": [card("O que o ROLLBACK faz?", "Desfaz.")]},
    ]

    corpo = gerar_cards(client, headers, disciplina_id, material_id=material_id, quantidade=1).json()

    assert corpo["geracao"]["chamadas"] == 2 and len(corpo["criados"]) == 1


def test_falha_de_validacao_nao_salva_cards_e_registra_erro(
    client, headers, disciplina_id, anthropic_falso, session
):
    material_id = enviar_material(client, headers, disciplina_id)
    invalido = {"flashcards": [card("O que o ROLLBACK faz?", "Desfaz.", trechos=["T7"])]}
    anthropic_falso.roteiro += [invalido, invalido]

    resposta = gerar_cards(client, headers, disciplina_id, material_id=material_id)

    assert resposta.status_code == 502
    assert session.scalar(select(func.count()).select_from(Flashcard)) == 0
    geracao = session.get(Geracao, resposta.json()["detail"]["geracao_id"])
    assert (geracao.tipo, geracao.status, geracao.chamadas) == ("flashcards", "erro_validacao", 2)


def test_material_e_tema_juntos_ou_nenhum_retorna_422(client, headers, disciplina_id):
    assert gerar_cards(client, headers, disciplina_id).status_code == 422
    assert gerar_cards(client, headers, disciplina_id, material_id=1, tema="x").status_code == 422


def test_material_ainda_nao_processado_retorna_409(client, headers, disciplina_id, session):
    pendente = Material(
        disciplina_id=disciplina_id, titulo="x", tipo="texto", hash_sha256="e" * 64, tamanho_bytes=1
    )
    session.add(pendente)
    session.flush()

    resposta = gerar_cards(client, headers, disciplina_id, material_id=pendente.id)

    assert resposta.status_code == 409


def test_material_de_outra_disciplina_retorna_404(client, headers, disciplina_id):
    outra = client.post("/disciplinas", json={"nome": "Outra"}, headers=headers).json()["id"]
    material_id = enviar_material(client, headers, outra)

    assert gerar_cards(client, headers, disciplina_id, material_id=material_id).status_code == 404


def test_listar_flashcards_com_trechos_de_origem(client, headers, disciplina_id, anthropic_falso):
    material_id = enviar_material(client, headers, disciplina_id)
    anthropic_falso.roteiro.append({"flashcards": [card("O que o ROLLBACK faz?", "Desfaz.")]})
    gerar_cards(client, headers, disciplina_id, material_id=material_id)

    cards = client.get(f"/disciplinas/{disciplina_id}/flashcards", headers=headers).json()

    assert len(cards) == 1 and cards[0]["trechos"][0]["material_id"] == material_id


def test_material_grande_manda_ate_30_trechos_espalhados(
    session, usuario, disciplina_id, client, embedder
):
    material = Material(
        disciplina_id=disciplina_id, titulo="Livro", tipo="texto", status="concluido",
        hash_sha256="f" * 64, tamanho_bytes=1,
    )
    session.add(material)
    session.flush()
    session.execute(
        text(
            "INSERT INTO trechos (material_id, disciplina_id, ordem, conteudo) "
            "SELECT :m, :d, g, 'trecho ' || g FROM generate_series(0, 99) AS g"
        ),
        {"m": material.id, "d": disciplina_id},
    )

    contexto = gerar.buscar_contexto(
        session, disciplina_id=disciplina_id, material_id=material.id, tema=None, embedder=embedder
    )

    ordens = [int(t.conteudo.split()[1]) for t in contexto.trechos.values()]
    assert len(ordens) == 25  # 100 trechos, passo ceil(100/30) = 4
    assert ordens[0] == 0 and ordens[-1] == 96  # do começo ao fim do material
