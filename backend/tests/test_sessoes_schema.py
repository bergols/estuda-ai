"""Regras das sessões de estudo que vivem no banco (fase 7).

Inserem direto pela sessão do ORM, sem a API: provam que o Postgres recusa dado
inválido mesmo que algum código esqueça de validar.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from app.models import Disciplina, EventoFoco, PausaSessao, SessaoEstudo

INICIO = datetime(2026, 10, 8, 13, 0, tzinfo=UTC)  # 10:00 em São Paulo


def _sessao(usuario_id: int, **campos) -> SessaoEstudo:
    padrao = dict(
        usuario_id=usuario_id,
        chave=uuid4(),
        metodo="pomodoro",
        foco_min=25,
        pausa_min=5,
        ciclos=4,
        sistema="macos",
        status="em_andamento",
        iniciada_em=INICIO,
    )
    return SessaoEstudo(**(padrao | campos))


def _deve_violar(session, objeto, constraint):
    with pytest.raises(IntegrityError) as erro:
        with session.begin_nested():  # SAVEPOINT: o erro não aborta o resto do teste
            session.add(objeto)
            session.flush()
    assert erro.value.orig.diag.constraint_name == constraint


@pytest.fixture
def disciplina(session, usuario):
    d = Disciplina(usuario_id=usuario.id, nome="Cálculo II")
    session.add(d)
    session.flush()
    return d


def test_duracoes_sao_colunas_geradas(session, usuario):
    s = _sessao(usuario.id, status="concluida", terminada_em=INICIO + timedelta(minutes=125))
    session.add(s)
    session.flush()
    session.refresh(s)
    assert s.duracao_planejada_s == 25 * 60 * 4  # foco planejado, sem as pausas
    assert s.duracao_real_s == 125 * 60  # do início ao fim, com as pausas


def test_coluna_gerada_nao_aceita_valor_escrito(session, usuario):
    s = _sessao(usuario.id)
    session.add(s)
    session.flush()
    with pytest.raises(ProgrammingError, match="generated column"):
        with session.begin_nested():
            session.execute(
                text("UPDATE sessoes_estudo SET duracao_planejada_s = 1 WHERE id = :id"), {"id": s.id}
            )


def test_sessao_em_andamento_nao_tem_duracao_real(session, usuario):
    s = _sessao(usuario.id)
    session.add(s)
    session.flush()
    session.refresh(s)
    assert s.duracao_real_s is None


def test_chave_de_idempotencia_unica_por_usuario(session, usuario, outro_usuario):
    chave = uuid4()
    session.add(_sessao(usuario.id, chave=chave))
    session.flush()
    _deve_violar(session, _sessao(usuario.id, chave=chave), "uq_sessoes_estudo_usuario_id_chave")
    # A mesma chave em OUTRO usuário não colide (nem encontra a sessão alheia)
    session.add(_sessao(outro_usuario.id, chave=chave))
    session.flush()


@pytest.mark.parametrize(
    ("campos", "constraint"),
    [
        ({"metodo": "maratona"}, "ck_sessoes_estudo_metodo_valido"),
        ({"metodo": "52_17", "foco_min": 50, "pausa_min": 17}, "ck_sessoes_estudo_metodo_52_17"),
        ({"metodo": "bloco", "foco_min": 60, "pausa_min": 5, "ciclos": 1}, "ck_sessoes_estudo_metodo_bloco"),
        ({"metodo": "bloco", "foco_min": 60, "pausa_min": 0, "ciclos": 2}, "ck_sessoes_estudo_metodo_bloco"),
        ({"foco_min": 0}, "ck_sessoes_estudo_foco_min_valido"),
        ({"foco_min": 241}, "ck_sessoes_estudo_foco_min_valido"),
        ({"ciclos": 13}, "ck_sessoes_estudo_ciclos_validos"),
        ({"pausa_longa_min": 15}, "ck_sessoes_estudo_pausa_longa_completa"),
        ({"meta": "   "}, "ck_sessoes_estudo_meta_valida"),
        ({"sistema": "android"}, "ck_sessoes_estudo_sistema_valido"),
        ({"status": "concluida"}, "ck_sessoes_estudo_fim_conforme_status"),  # sem terminada_em
        ({"terminada_em": INICIO + timedelta(hours=1)}, "ck_sessoes_estudo_fim_conforme_status"),
        (
            {"status": "concluida", "terminada_em": INICIO - timedelta(seconds=1)},
            "ck_sessoes_estudo_fim_apos_inicio",
        ),
    ],
)
def test_regras_da_sessao(session, usuario, campos, constraint):
    _deve_violar(session, _sessao(usuario.id, **campos), constraint)


def test_metodos_validos_passam(session, usuario):
    for campos in [
        {"metodo": "52_17", "foco_min": 52, "pausa_min": 17, "ciclos": 2},
        {"metodo": "bloco", "foco_min": 60, "pausa_min": 0, "ciclos": 1, "meta": "lista 3"},
        {"pausa_longa_min": 15, "ciclos_ate_pausa_longa": 4},
        {"metodo": "personalizado", "foco_min": 40, "pausa_min": 10, "ciclos": 3},
    ]:
        session.add(_sessao(usuario.id, **campos))
    session.flush()


def test_nao_liga_a_disciplina_de_outro_usuario(session, outro_usuario, disciplina):
    _deve_violar(
        session,
        _sessao(outro_usuario.id, disciplina_id=disciplina.id),
        "fk_sessoes_estudo_disciplina_id_disciplinas",
    )


def test_apagar_a_disciplina_mantem_a_sessao_e_o_dono(session, usuario, disciplina):
    s = _sessao(usuario.id, disciplina_id=disciplina.id)
    session.add(s)
    session.flush()
    session.delete(disciplina)
    session.flush()
    linha = session.execute(
        select(SessaoEstudo.disciplina_id, SessaoEstudo.usuario_id).where(SessaoEstudo.id == s.id)
    ).one()
    assert linha.disciplina_id is None  # as horas estudadas continuam no histórico
    assert linha.usuario_id == usuario.id  # SET NULL (disciplina_id) não toca no dono


def test_pausa_e_evento_unicos_por_sessao(session, usuario):
    s = _sessao(usuario.id)
    session.add(s)
    session.flush()
    chave = uuid4()
    pausa = dict(sessao_id=s.id, chave=chave, tipo="curta", iniciada_em=INICIO,
                 terminada_em=INICIO + timedelta(minutes=5))
    session.add(PausaSessao(**pausa))
    session.flush()
    _deve_violar(session, PausaSessao(**pausa), "uq_pausas_sessao_sessao_id_chave")

    evento = dict(sessao_id=s.id, chave=chave, tipo="programa_bloqueado", ocorrido_em=INICIO,
                  detalhe="Discord.exe")
    session.add(EventoFoco(**evento))  # mesma chave da pausa: tabelas diferentes, sem conflito
    session.flush()
    _deve_violar(session, EventoFoco(**evento), "uq_eventos_foco_sessao_id_chave")


@pytest.mark.parametrize(
    ("campos", "constraint"),
    [
        ({"tipo": "saida_janela"}, "ck_eventos_foco_duracao_so_na_saida"),
        ({"tipo": "programa_bloqueado", "duracao_s": 10}, "ck_eventos_foco_duracao_so_na_saida"),
        ({"tipo": "saida_janela", "duracao_s": -1}, "ck_eventos_foco_duracao_nao_negativa"),
        ({"tipo": "notificacao"}, "ck_eventos_foco_tipo_valido"),
        ({"tipo": "site_bloqueado", "detalhe": ""}, "ck_eventos_foco_detalhe_valido"),
    ],
)
def test_regras_do_evento(session, usuario, campos, constraint):
    s = _sessao(usuario.id)
    session.add(s)
    session.flush()
    _deve_violar(session, EventoFoco(sessao_id=s.id, chave=uuid4(), ocorrido_em=INICIO, **campos), constraint)


def test_pausa_nao_termina_antes_de_comecar(session, usuario):
    s = _sessao(usuario.id)
    session.add(s)
    session.flush()
    _deve_violar(
        session,
        PausaSessao(sessao_id=s.id, chave=uuid4(), tipo="curta", iniciada_em=INICIO,
                    terminada_em=INICIO - timedelta(seconds=1)),
        "ck_pausas_sessao_fim_apos_inicio",
    )


def test_view_calcula_foco_efetivo_e_interrupcoes(session, usuario):
    # 2 h de sessão: 2 pausas de 10 min, 5 min fora da janela, 1 programa bloqueado
    s = _sessao(usuario.id, status="abandonada", terminada_em=INICIO + timedelta(hours=2))
    session.add(s)
    session.flush()
    for i in range(2):
        ini = INICIO + timedelta(minutes=25 + 35 * i)
        session.add(PausaSessao(sessao_id=s.id, chave=uuid4(), tipo="curta", iniciada_em=ini,
                                terminada_em=ini + timedelta(minutes=10)))
    session.add_all([
        EventoFoco(sessao_id=s.id, chave=uuid4(), tipo="saida_janela", ocorrido_em=INICIO,
                   duracao_s=300),
        EventoFoco(sessao_id=s.id, chave=uuid4(), tipo="programa_bloqueado", ocorrido_em=INICIO,
                   detalhe="Discord"),
        EventoFoco(sessao_id=s.id, chave=uuid4(), tipo="saida_emergencia", ocorrido_em=INICIO),
    ])
    session.flush()
    linha = session.execute(
        text("SELECT * FROM vw_sessoes_foco WHERE id = :id"), {"id": s.id}
    ).mappings().one()
    assert linha["duracao_real_s"] == 7200
    assert linha["pausas_s"] == 1200
    assert linha["fora_s"] == 300
    assert linha["foco_efetivo_s"] == 7200 - 1200 - 300
    assert linha["interrupcoes"] == 2  # saída da janela + programa (a emergência conta à parte)
    assert linha["emergencias"] == 1
    assert str(linha["dia"]) == "2026-10-08"  # no fuso do usuário


def test_view_de_sessao_sem_pausas_nem_eventos(session, usuario):
    s = _sessao(usuario.id)
    session.add(s)
    session.flush()
    linha = session.execute(
        text("SELECT pausas_s, fora_s, interrupcoes, foco_efetivo_s FROM vw_sessoes_foco WHERE id = :id"),
        {"id": s.id},
    ).one()
    # COALESCE nas somas: sem linhas, 0 (e não NULL); em andamento, foco efetivo NULL
    assert tuple(linha) == (0, 0, 0, None)
