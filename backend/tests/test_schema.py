"""Testes das regras que vivem no banco (constraints, ON DELETE, tipos).

Não passam pela API: inserem direto pela sessão, para provar que o Postgres
recusa dados inválidos mesmo que algum código esqueça de validar.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DataError, DBAPIError, IntegrityError

from app.models import (
    Alternativa,
    Disciplina,
    Flashcard,
    Geracao,
    HistoricoRevisao,
    Material,
    Questao,
    Revisao,
    Tentativa,
    Trecho,
    Usuario,
)


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


def _verificar_constraints_adiadas(session):
    """Faz agora a verificação que o COMMIT faria.

    Os testes nunca dão COMMIT de verdade (tudo é desfeito no fim), então as
    constraints DEFERRABLE INITIALLY DEFERRED ficariam esperando para sempre.
    SET CONSTRAINTS ALL IMMEDIATE dispara as verificações pendentes na hora;
    depois voltamos ao modo adiado para o resto do teste.
    """
    session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    session.execute(text("SET CONSTRAINTS ALL DEFERRED"))


def _questao_mc(session, disciplina, alternativas: list[tuple[str, bool]]) -> Questao:
    q = Questao(
        disciplina_id=disciplina.id,
        enunciado="Qual índice é o padrão?",
        tipo="multipla_escolha",
        alternativas=[
            Alternativa(letra=chr(65 + i), texto=texto, correta=correta)
            for i, (texto, correta) in enumerate(alternativas)
        ],
    )
    session.add(q)
    session.flush()
    return q


@pytest.mark.parametrize(
    "alternativas",
    [
        [],  # nenhuma alternativa
        [("B-tree", True)],  # só uma
        [("B-tree", False), ("Hash", False)],  # nenhuma correta
    ],
)
def test_multipla_escolha_invalida_e_barrada_no_commit(session, disciplina, alternativas):
    with pytest.raises(IntegrityError) as erro:
        with session.begin_nested():
            _questao_mc(session, disciplina, alternativas)  # os INSERTs passam...
            _verificar_constraints_adiadas(session)  # ...a verificação do COMMIT não
    assert erro.value.orig.diag.constraint_name == "ck_questoes_alternativas_validas"


def test_multipla_escolha_valida_passa(session, disciplina):
    _questao_mc(session, disciplina, [("B-tree", True), ("Hash", False)])
    _verificar_constraints_adiadas(session)


def test_duas_alternativas_corretas_sao_barradas_na_hora(session, disciplina):
    q = _questao_mc(session, disciplina, [("B-tree", True), ("Hash", False)])

    # O índice único parcial NÃO é adiado: barra já no UPDATE.
    with pytest.raises(IntegrityError) as erro:
        with session.begin_nested():
            session.execute(
                text("UPDATE alternativas SET correta = true WHERE questao_id = :q"), {"q": q.id}
            )
    assert erro.value.orig.diag.constraint_name == "uq_alternativas_uma_correta"


def test_questao_que_nao_e_multipla_escolha_nao_tem_alternativas(session, disciplina):
    q = Questao(
        disciplina_id=disciplina.id, enunciado="V ou F?", tipo="verdadeiro_falso",
        resposta_correta="V", alternativas=[Alternativa(letra="A", texto="x")],
    )
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(q)
            session.flush()
            _verificar_constraints_adiadas(session)


def test_gabarito_de_multipla_escolha_mora_nas_alternativas(session, disciplina):
    q = Questao(
        disciplina_id=disciplina.id, enunciado="Q?", tipo="multipla_escolha", resposta_correta="A"
    )
    _deve_violar(session, q, "ck_questoes_gabarito_conforme_tipo")


def test_tentativa_nao_aceita_alternativa_de_outra_questao(session, disciplina):
    q1 = _questao_mc(session, disciplina, [("B-tree", True), ("Hash", False)])
    q2 = _questao_mc(session, disciplina, [("GIN", True), ("GiST", False)])

    alheia = Tentativa(questao_id=q1.id, alternativa_id=q2.alternativas[0].id, correta=True)

    _deve_violar(session, alheia, "fk_tentativas_questao_id_alternativas")


def test_geracao_sobrevive_a_disciplina_apagada_e_mantem_o_dono(session, usuario, disciplina):
    g = Geracao(
        usuario_id=usuario.id, disciplina_id=disciplina.id, tipo="pergunta",
        modelo="claude-haiku-4-5-20251001", duracao_ms=10, status="sucesso", custo_usd=0.001,
    )
    session.add(g)
    session.flush()

    session.execute(text("DELETE FROM disciplinas WHERE id = :id"), {"id": disciplina.id})

    linha = session.execute(
        text("SELECT usuario_id, disciplina_id FROM geracoes WHERE id = :id"), {"id": g.id}
    ).one()
    assert linha.usuario_id == usuario.id  # SET NULL (disciplina_id) não toca no dono
    assert linha.disciplina_id is None


def test_geracao_nao_aceita_disciplina_de_outro_usuario(session, outro_usuario, disciplina):
    g = Geracao(
        usuario_id=outro_usuario.id, disciplina_id=disciplina.id, tipo="pergunta",
        modelo="x", duracao_ms=1, status="sucesso",
    )
    _deve_violar(session, g, "fk_geracoes_disciplina_id_disciplinas")


def _card(session, disciplina, frente="f") -> Flashcard:
    card = Flashcard(disciplina_id=disciplina.id, frente=frente, verso="v")
    session.add(card)
    session.flush()
    return card


def test_card_novo_nasce_com_estado_do_sm2(session, disciplina):
    card = _card(session, disciplina)

    estado = session.get(Revisao, card.id)  # criado pelo trigger, não pelo ORM

    assert estado.disciplina_id == disciplina.id
    assert (estado.facilidade, estado.intervalo_dias, estado.repeticoes, estado.versao) == (
        Decimal("2.50"), 0, 0, 0,
    )
    assert estado.ultima_revisao_em is None


def test_facilidade_do_sm2_nao_fica_abaixo_de_1_3(session, disciplina):
    card = _card(session, disciplina)

    with pytest.raises(IntegrityError) as erro:
        with session.begin_nested():
            session.execute(
                text("UPDATE revisoes SET facilidade = 1.2 WHERE flashcard_id = :id"), {"id": card.id}
            )
    assert erro.value.orig.diag.constraint_name == "ck_revisoes_facilidade_minima"


def test_versao_zero_significa_nunca_revisado(session, disciplina):
    card = _card(session, disciplina)

    with pytest.raises(IntegrityError) as erro:
        with session.begin_nested():
            session.execute(
                text("UPDATE revisoes SET versao = 1 WHERE flashcard_id = :id"), {"id": card.id}
            )
    assert erro.value.orig.diag.constraint_name == "ck_revisoes_versao_conforme_ultima_revisao"


def test_estado_nao_aceita_disciplina_diferente_da_do_card(session, usuario, disciplina):
    card = _card(session, disciplina)
    outra = Disciplina(usuario_id=usuario.id, nome="Outra")
    session.add(outra)
    session.flush()

    with pytest.raises(IntegrityError) as erro:
        with session.begin_nested():
            session.execute(
                text("UPDATE revisoes SET disciplina_id = :d WHERE flashcard_id = :id"),
                {"d": outra.id, "id": card.id},
            )
    assert erro.value.orig.diag.constraint_name == "fk_revisoes_flashcard_id_flashcards"


def test_mover_card_de_disciplina_leva_o_estado_junto(session, usuario, disciplina):
    card = _card(session, disciplina)
    outra = Disciplina(usuario_id=usuario.id, nome="Outra")
    session.add(outra)
    session.flush()

    session.execute(
        text("UPDATE flashcards SET disciplina_id = :d WHERE id = :id"), {"d": outra.id, "id": card.id}
    )

    disciplina_do_estado = session.scalar(
        text("SELECT disciplina_id FROM revisoes WHERE flashcard_id = :id"), {"id": card.id}
    )
    assert disciplina_do_estado == outra.id  # ON UPDATE CASCADE da FK composta


def test_historico_de_revisoes_e_imutavel(session, disciplina):
    card = _card(session, disciplina)
    h = HistoricoRevisao(
        flashcard_id=card.id, nota=4, facilidade_anterior=Decimal("2.50"),
        facilidade_nova=Decimal("2.50"), intervalo_anterior=0, intervalo_novo=1,
        repeticoes_anterior=0, repeticoes_nova=1,
        proxima_revisao_anterior=datetime(2026, 10, 1, tzinfo=UTC),
        proxima_revisao_nova=datetime(2026, 10, 2, tzinfo=UTC),
        revisado_em=datetime(2026, 10, 1, tzinfo=UTC),
    )
    session.add(h)
    session.flush()

    with pytest.raises(DBAPIError, match="imutável"):
        with session.begin_nested():
            session.execute(text("UPDATE historico_revisoes SET nota = 5 WHERE id = :id"), {"id": h.id})


def test_apagar_card_apaga_estado_e_historico(session, disciplina):
    card = _card(session, disciplina)

    session.execute(text("DELETE FROM flashcards WHERE id = :id"), {"id": card.id})

    assert session.get(Revisao, card.id) is None


def test_fuso_horario_invalido_e_recusado(session, usuario):
    with pytest.raises(DBAPIError, match="not recognized"):
        with session.begin_nested():
            session.execute(
                text("UPDATE usuarios SET fuso_horario = 'Marte/Olympus' WHERE id = :id"),
                {"id": usuario.id},
            )
    assert session.get(Usuario, usuario.id).fuso_horario == "America/Sao_Paulo"


def test_embedding_precisa_ter_384_dimensoes(session, trecho):
    with pytest.raises(DataError, match="expected 384 dimensions"):
        with session.begin_nested():
            trecho.embedding = [0.1, 0.2, 0.3]
            session.flush()


def test_apagar_trecho_desvincula_o_flashcard_sem_apagar(session, disciplina, trecho):
    card = Flashcard(disciplina_id=disciplina.id, frente="f", verso="v", trechos=[trecho])
    session.add(card)
    session.flush()

    session.execute(text("DELETE FROM trechos WHERE id = :id"), {"id": trecho.id})

    # A linha da tabela associativa some (CASCADE); o flashcard continua.
    associacoes = session.scalar(
        text("SELECT count(*) FROM flashcard_trechos WHERE flashcard_id = :id"), {"id": card.id}
    )
    assert associacoes == 0
    assert session.scalar(select(Flashcard.id).where(Flashcard.id == card.id)) == card.id


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
