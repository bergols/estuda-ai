"""Duas requisições de revisão do MESMO card ao mesmo tempo (duplo clique, duas abas).

Diferente dos outros testes, estes usam conexões REAIS e dados COMMITADOS: cada
thread tem a própria sessão, como duas requisições numa API de verdade. A
transação-que-é-desfeita do conftest não serve aqui, porque uma conexão não
enxerga o que a outra não commitou.

A Barrier faz as duas threads lerem o estado ANTES de qualquer uma gravar: é a
janela da condição de corrida, forçada para o teste ser determinístico.
"""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

import app.servicos.revisao as revisao
from app.servicos.sm2 import EstadoSM2, calcular, proxima_revisao


@pytest.fixture
def card(engine):
    """Usuário + disciplina + card commitados; apagados no fim (cascata)."""
    with Session(engine) as s:
        usuario_id = s.execute(
            text("INSERT INTO usuarios (nome, email) VALUES ('Concorrência', :e) RETURNING id"),
            {"e": f"conc-{uuid.uuid4().hex[:8]}@x.com"},
        ).scalar_one()
        disciplina_id = s.execute(
            text("INSERT INTO disciplinas (usuario_id, nome) VALUES (:u, 'BD') RETURNING id"),
            {"u": usuario_id},
        ).scalar_one()
        flashcard_id = s.execute(
            text("INSERT INTO flashcards (disciplina_id, frente, verso) "
                 "VALUES (:d, 'O que é MVCC?', 'Versões de linha.') RETURNING id"),
            {"d": disciplina_id},
        ).scalar_one()
        s.commit()
    yield usuario_id, flashcard_id
    with Session(engine) as s:
        s.execute(text("DELETE FROM usuarios WHERE id = :u"), {"u": usuario_id})
        s.commit()


def situacao(engine, flashcard_id):
    with Session(engine) as s:
        return s.execute(
            text("""SELECT r.versao, r.repeticoes,
                           (SELECT count(*) FROM historico_revisoes h WHERE h.flashcard_id = r.flashcard_id)
                    FROM revisoes r WHERE r.flashcard_id = :f"""),
            {"f": flashcard_id},
        ).one()


def em_paralelo(funcao):
    with ThreadPoolExecutor(2) as executor:
        return list(executor.map(lambda _: funcao(), range(2)))


def gravar(s, flashcard_id, anterior, novo, proxima_anterior):
    """UPDATE do estado + INSERT no histórico, SEM checar versão."""
    agora = s.execute(text("SELECT now()")).scalar_one()
    proxima = proxima_revisao(agora, novo.intervalo)
    s.execute(
        text("""UPDATE revisoes SET facilidade = :fa, intervalo_dias = :i, repeticoes = :r,
                    proxima_revisao = :p, ultima_revisao_em = :agora, versao = versao + 1
                WHERE flashcard_id = :f"""),
        {"fa": novo.facilidade, "i": novo.intervalo, "r": novo.repeticoes, "p": proxima,
         "agora": agora, "f": flashcard_id},
    )
    revisao._inserir_historico(s, {
        "flashcard_id": flashcard_id, "nota": 5,
        "facilidade_anterior": anterior.facilidade, "facilidade": novo.facilidade,
        "intervalo_anterior": anterior.intervalo, "intervalo": novo.intervalo,
        "repeticoes_anterior": anterior.repeticoes, "repeticoes": novo.repeticoes,
        "proxima_anterior": proxima_anterior, "proxima": proxima, "agora": agora,
    })
    s.commit()


SQL_LER = "SELECT facilidade, intervalo_dias, repeticoes, proxima_revisao FROM revisoes WHERE flashcard_id = :f"


# ------------------------------------------------- o problema (sem controle)


def test_sem_controle_o_card_e_processado_em_dobro_e_perde_atualizacao(engine, card):
    """Leitura -> cálculo -> escrita sem proteção: as duas leem repeticoes=0,
    as duas gravam. Resultado: 2 linhas no histórico (processado em dobro) e
    repeticoes=1 em vez de 2 (lost update: a 2a escrita apagou a 1a)."""
    _, flashcard_id = card
    barreira = threading.Barrier(2, timeout=10)

    def revisar_sem_controle():
        with Session(engine) as s:
            linha = s.execute(text(SQL_LER), {"f": flashcard_id}).one()
            anterior = EstadoSM2(Decimal(linha.facilidade), linha.intervalo_dias, linha.repeticoes)
            barreira.wait()  # as duas já leram
            gravar(s, flashcard_id, anterior, calcular(anterior, 5), linha.proxima_revisao)

    em_paralelo(revisar_sem_controle)

    versao, repeticoes, historico = situacao(engine, flashcard_id)
    assert historico == 2  # processado em dobro
    assert repeticoes == 1  # lost update


def test_for_update_serializa_mas_ainda_processa_em_dobro(engine, card):
    """SELECT ... FOR UPDATE: a 2a requisição ESPERA a 1a terminar e então lê o
    estado novo. Não há lost update (repeticoes chega a 2), mas o card é revisado
    duas vezes: o lock ordena as requisições, não sabe que a 2a é repetida."""
    _, flashcard_id = card
    largada = threading.Barrier(2, timeout=10)

    def revisar_com_for_update():
        largada.wait()  # começam juntas
        with Session(engine) as s:
            linha = s.execute(text(SQL_LER + " FOR UPDATE"), {"f": flashcard_id}).one()
            anterior = EstadoSM2(Decimal(linha.facilidade), linha.intervalo_dias, linha.repeticoes)
            gravar(s, flashcard_id, anterior, calcular(anterior, 5), linha.proxima_revisao)

    em_paralelo(revisar_com_for_update)

    versao, repeticoes, historico = situacao(engine, flashcard_id)
    assert (repeticoes, historico) == (2, 2)  # sem lost update, mas em dobro


# --------------------------------------------- a solução (controle otimista)


def test_controle_otimista_processa_uma_vez_e_a_outra_recebe_409(engine, card, monkeypatch):
    usuario_id, flashcard_id = card
    barreira = threading.Barrier(2, timeout=10)

    def calcular_depois_que_as_duas_leram(*args, **kwargs):
        barreira.wait()  # registrar_revisao chama calcular() entre a leitura e o UPDATE
        return calcular(*args, **kwargs)

    monkeypatch.setattr(revisao, "calcular", calcular_depois_que_as_duas_leram)

    def requisicao():
        with Session(engine) as s:
            try:
                revisao.registrar_revisao(
                    s, usuario_id=usuario_id, flashcard_id=flashcard_id, nota=5, versao=0
                )
                return "200"
            except revisao.ErroRevisao as erro:
                return str(erro.status)

    resultados = sorted(em_paralelo(requisicao))

    assert resultados == ["200", "409"]
    versao, repeticoes, historico = situacao(engine, flashcard_id)
    assert (versao, repeticoes, historico) == (1, 1, 1)  # uma revisão, só uma
