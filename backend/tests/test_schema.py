"""Testes das regras que vivem no banco (constraints, ON DELETE, tipos).

Não passam pela API: inserem direto pela sessão, para provar que o Postgres
recusa dados inválidos mesmo que algum código esqueça de validar.
"""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DataError, IntegrityError

from app.models import Disciplina, Flashcard, Material, Questao, Trecho


@pytest.fixture
def disciplina(session, usuario):
    d = Disciplina(usuario_id=usuario.id, nome="Banco de Dados")
    session.add(d)
    session.flush()
    return d


@pytest.fixture
def trecho(session, disciplina):
    material = Material(
        disciplina_id=disciplina.id,
        titulo="Apostila",
        tipo="pdf",
        hash_sha256="a" * 64,
        tamanho_bytes=1024,
        caminho_arquivo="apostila.pdf",
    )
    session.add(material)
    session.flush()
    t = Trecho(
        material_id=material.id,
        disciplina_id=disciplina.id,
        ordem=0,
        conteudo="Índices B-tree...",
    )
    session.add(t)
    session.flush()
    return t


def _deve_violar(session, objeto, constraint):
    """Tenta inserir e confirma que a constraint esperada barrou."""
    with pytest.raises(IntegrityError) as erro:
        with session.begin_nested():  # SAVEPOINT: o erro não aborta o resto do teste
            session.add(objeto)
            session.flush()
    assert erro.value.orig.diag.constraint_name == constraint


def test_multipla_escolha_exige_lista_de_alternativas(session, disciplina):
    base = dict(disciplina_id=disciplina.id, enunciado="Q?", resposta_correta="A")
    ck = "ck_questoes_alternativas_conforme_tipo"

    _deve_violar(session, Questao(**base, tipo="multipla_escolha"), ck)
    _deve_violar(session, Questao(**base, tipo="multipla_escolha", alternativas=["A"]), ck)
    _deve_violar(session, Questao(**base, tipo="dissertativa", alternativas=["A", "B"]), ck)

    session.add(Questao(**base, tipo="multipla_escolha", alternativas=["A", "B"]))
    session.flush()


def test_facilidade_do_sm2_nao_fica_abaixo_de_1_3(session, disciplina):
    card = Flashcard(disciplina_id=disciplina.id, frente="f", verso="v", facilidade=1.2)
    _deve_violar(session, card, "ck_flashcards_facilidade_minima")


def test_embedding_precisa_ter_384_dimensoes(session, trecho):
    with pytest.raises(DataError, match="expected 384 dimensions"):
        with session.begin_nested():
            trecho.embedding = [0.1, 0.2, 0.3]
            session.flush()


def test_apagar_trecho_desvincula_o_flashcard_sem_apagar(session, disciplina, trecho):
    card = Flashcard(disciplina_id=disciplina.id, trecho_id=trecho.id, frente="f", verso="v")
    session.add(card)
    session.flush()

    session.execute(text("DELETE FROM trechos WHERE id = :id"), {"id": trecho.id})

    trecho_id = session.scalar(select(Flashcard.trecho_id).where(Flashcard.id == card.id))
    assert trecho_id is None  # ON DELETE SET NULL


def test_apagar_disciplina_apaga_conteudo_em_cascata(session, disciplina, trecho):
    session.add(Flashcard(disciplina_id=disciplina.id, frente="f", verso="v"))
    session.flush()

    session.execute(text("DELETE FROM disciplinas WHERE id = :id"), {"id": disciplina.id})

    for tabela in ("materiais", "trechos", "flashcards"):
        assert session.scalar(text(f"SELECT count(*) FROM {tabela}")) == 0


def test_pdf_sem_caminho_de_arquivo_e_recusado(session, disciplina):
    material = Material(
        disciplina_id=disciplina.id, titulo="x", tipo="pdf", hash_sha256="c" * 64, tamanho_bytes=1
    )
    _deve_violar(session, material, "ck_materiais_pdf_tem_arquivo")


def test_mensagem_de_erro_so_com_status_erro(session, disciplina):
    material = Material(
        disciplina_id=disciplina.id,
        titulo="x",
        tipo="texto",
        hash_sha256="d" * 64,
        tamanho_bytes=1,
        status="concluido",
        erro_mensagem="isso não deveria existir",
    )
    _deve_violar(session, material, "ck_materiais_erro_so_com_status_erro")


def test_fk_composta_impede_trecho_com_disciplina_diferente_da_do_material(
    session, usuario, trecho
):
    outra = Disciplina(usuario_id=usuario.id, nome="Outra")
    session.add(outra)
    session.flush()

    copia_errada = Trecho(
        material_id=trecho.material_id, disciplina_id=outra.id, ordem=1, conteudo="x"
    )
    _deve_violar(session, copia_errada, "fk_trechos_material_id_materiais")


def test_mover_material_de_disciplina_atualiza_os_trechos(session, usuario, trecho):
    outra = Disciplina(usuario_id=usuario.id, nome="Outra")
    session.add(outra)
    session.flush()

    session.execute(
        text("UPDATE materiais SET disciplina_id = :d WHERE id = :m"),
        {"d": outra.id, "m": trecho.material_id},
    )

    disciplina_do_trecho = session.scalar(
        text("SELECT disciplina_id FROM trechos WHERE id = :id"), {"id": trecho.id}
    )
    assert disciplina_do_trecho == outra.id  # ON UPDATE CASCADE da FK composta
