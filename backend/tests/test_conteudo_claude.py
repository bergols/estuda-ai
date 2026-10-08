"""scripts/conteudo_claude.py: conteúdo escrito no Claude Code, gravado pelas regras do app."""

from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text

from app.models import Disciplina, Flashcard, Geracao, Material, Questao, Trecho
from scripts.conteudo_claude import MODELO, ErroLote, Lote, importar_lote


@pytest.fixture
def material(session, usuario):
    d = Disciplina(usuario_id=usuario.id, nome="Banco de Dados")
    session.add(d)
    session.flush()
    m = Material(disciplina_id=d.id, titulo="Aula de índices", tipo="texto", status="concluido",
                 hash_sha256="c" * 64, tamanho_bytes=10)
    session.add(m)
    session.flush()
    trechos = [Trecho(material_id=m.id, disciplina_id=d.id, ordem=i, conteudo=f"conteúdo {i}")
               for i in range(3)]
    session.add_all(trechos)
    session.flush()
    return d, [t.id for t in trechos]


def card(frente, verso, trechos):
    return {"frente": frente, "verso": verso, "topico": "Índices", "trechos": trechos}


def questao(trechos, correta="B"):
    return {"enunciado": "Qual estrutura o Postgres usa por padrão em CREATE INDEX?",
            "alternativas": ["Hash", "B-tree", "GIN", "BRIN"], "correta": correta,
            "explicacao": "O tipo padrão é B-tree.", "dificuldade": 2, "topico": "Índices",
            "trechos": trechos}


def test_grava_cards_e_questoes_com_trechos_e_auditoria_de_custo_zero(session, usuario, embedder, material):
    d, trechos = material
    lote = Lote.model_validate({
        "disciplina_id": d.id,
        "flashcards": [card("O que é um índice?", "Estrutura que acelera buscas.", trechos[:2])],
        "questoes": [questao([trechos[2]])],
    })

    resumo = importar_lote(session, usuario, lote, embedder)
    session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))  # confere a questão (1 correta)

    assert "1 flashcards criados" in resumo and "1 questões criadas" in resumo
    c = session.scalar(select(Flashcard).where(Flashcard.disciplina_id == d.id))
    assert {t.id for t in c.trechos} == set(trechos[:2]) and c.origem == "ia"
    q = session.scalar(select(Questao).where(Questao.disciplina_id == d.id))
    assert [a.correta for a in q.alternativas] == [False, True, False, False]
    geracoes = session.scalars(select(Geracao).where(Geracao.disciplina_id == d.id)).all()
    assert {g.tipo for g in geracoes} == {"flashcards", "questoes"}
    assert all(g.modelo == MODELO and g.custo_usd == Decimal("0") for g in geracoes)
    # o card entra na fila de revisão (estado SM-2 criado pelo trigger)
    assert session.scalar(text("SELECT count(*) FROM revisoes WHERE flashcard_id = :f"), {"f": c.id}) == 1


def test_duplicata_e_descartada_como_na_geracao_pela_api(session, usuario, embedder, material):
    d, trechos = material
    importar_lote(session, usuario, Lote.model_validate(
        {"disciplina_id": d.id, "flashcards": [card("O que é MVCC?", "Versões de linha.", trechos[:1])]}
    ), embedder)

    resumo = importar_lote(session, usuario, Lote.model_validate(
        {"disciplina_id": d.id, "flashcards": [card("O que é MVCC?", "Versões de linha.", trechos[:1])]}
    ), embedder)

    assert "0 flashcards criados" in resumo
    assert any("descartado" in linha for linha in resumo)


def test_trecho_de_outra_disciplina_e_recusado(session, usuario, outro_usuario, embedder, material):
    d, _ = material
    alheia = Disciplina(usuario_id=outro_usuario.id, nome="Alheia")
    session.add(alheia)
    session.flush()
    m = Material(disciplina_id=alheia.id, titulo="x", tipo="texto", status="concluido",
                 hash_sha256="d" * 64, tamanho_bytes=1)
    session.add(m)
    session.flush()
    t = Trecho(material_id=m.id, disciplina_id=alheia.id, ordem=0, conteudo="segredo")
    session.add(t)
    session.flush()

    with pytest.raises(ErroLote, match="trechos que não são"):
        importar_lote(session, usuario, Lote.model_validate(
            {"disciplina_id": d.id, "flashcards": [card("Pergunta?", "Resposta.", [t.id])]}
        ), embedder)


def test_disciplina_de_outra_conta_e_recusada(session, usuario, outro_usuario, embedder):
    alheia = Disciplina(usuario_id=outro_usuario.id, nome="Alheia")
    session.add(alheia)
    session.flush()
    with pytest.raises(ErroLote, match="não existe nesta conta"):
        importar_lote(session, usuario, Lote.model_validate(
            {"disciplina_id": alheia.id, "flashcards": [card("Pergunta?", "Resposta.", [1])]}
        ), embedder)


@pytest.mark.parametrize("mudanca", [
    {"alternativas": ["A", "B", "C"]},                       # 3 alternativas
    {"alternativas": ["Hash", "hash", "GIN", "BRIN"]},       # repetidas
    {"correta": "E"},                                        # letra inexistente
    {"trechos": []},                                         # sem fonte
])
def test_questao_invalida_nem_chega_ao_banco(mudanca):
    with pytest.raises(ValidationError):
        Lote.model_validate({"disciplina_id": 1, "questoes": [{**questao([1]), **mudanca}]})


def test_lote_vazio_e_recusado():
    with pytest.raises(ValidationError, match="não tem flashcards nem questões"):
        Lote.model_validate({"disciplina_id": 1})
