from decimal import Decimal
from itertools import product

import pytest
from sqlalchemy import text

from app.servicos.sm2 import EstadoSM2, calcular

D = Decimal


# ------------------------------------------------- cada nota, a partir do zero


@pytest.mark.parametrize(
    ("nota", "esperado"),
    [
        # nota: (facilidade, intervalo, repetições) depois da 1a revisão de um card novo
        (5, (D("2.60"), 1, 1)),  # +0,10
        (4, (D("2.50"), 1, 1)),  # +0,00
        (3, (D("2.36"), 1, 1)),  # -0,14
        (2, (D("2.18"), 1, 0)),  # -0,32 e recomeça
        (1, (D("1.96"), 1, 0)),  # -0,54
        (0, (D("1.70"), 1, 0)),  # -0,80
    ],
)
def test_cada_nota_num_card_novo(nota, esperado):
    novo = calcular(EstadoSM2(), nota)
    assert (novo.facilidade, novo.intervalo, novo.repeticoes) == esperado


def test_sequencia_de_acertos_segue_1_6_e_depois_multiplica():
    estado = EstadoSM2()
    intervalos = []
    for _ in range(5):
        estado = calcular(estado, 4)  # nota 4 mantém EF = 2,5
        intervalos.append(estado.intervalo)
    # 1, 6, 6*2,5=15, 15*2,5=37,5->38, 38*2,5=95
    assert intervalos == [1, 6, 15, 38, 95]


def test_esquecer_volta_ao_comeco_mas_a_facilidade_cai():
    maduro = EstadoSM2(D("2.50"), 38, 4)

    depois = calcular(maduro, 1)

    assert (depois.intervalo, depois.repeticoes) == (1, 0)
    assert depois.facilidade == D("1.96")
    assert calcular(depois, 4).intervalo == 1  # recomeça do 1


def test_facilidade_nunca_fica_abaixo_de_1_3():
    assert calcular(EstadoSM2(D("1.40"), 1, 0), 0).facilidade == D("1.30")


def test_meio_arredonda_para_cima_como_no_postgres():
    # 5 * 2,5 = 12,5: o round() do Python daria 12 (meio para o par); o banco dá 13
    assert calcular(EstadoSM2(D("2.50"), 5, 3), 4).intervalo == 13


@pytest.mark.parametrize("nota", [-1, 6])
def test_nota_fora_de_0_a_5_e_recusada(nota):
    with pytest.raises(ValueError):
        calcular(EstadoSM2(), nota)


# ------------------------------------------- Python x PL/pgSQL: mesmo resultado


ESTADOS = [
    EstadoSM2(f, i, r)
    for f, (i, r) in product(
        [D("1.30"), D("1.36"), D("2.15"), D("2.50"), D("2.55"), D("2.70"), D("3.10")],
        [(0, 0), (1, 1), (6, 2), (5, 3), (15, 3), (37, 4), (95, 6), (1, 0)],
    )
]


def test_python_e_plpgsql_concordam_em_todo_o_grid(session):
    """56 estados x 6 notas = 336 casos, numa única consulta ao banco."""
    casos = [(e, nota) for e in ESTADOS for nota in range(6)]
    valores = ", ".join(
        f"({i}, {e.facilidade}, {e.intervalo}, {e.repeticoes}, {nota})"
        for i, (e, nota) in enumerate(casos)
    )
    linhas = session.execute(
        text(f"""
            SELECT c.i, s.facilidade, s.intervalo, s.repeticoes
            FROM (VALUES {valores}) AS c(i, f, iv, r, nota)
            CROSS JOIN LATERAL sm2(c.f, c.iv, c.r, c.nota) AS s
            ORDER BY c.i
        """)
    ).all()

    divergencias = []
    for (estado, nota), linha in zip(casos, linhas, strict=True):
        py = calcular(estado, nota)
        if (py.facilidade, py.intervalo, py.repeticoes) != (
            linha.facilidade, linha.intervalo, linha.repeticoes
        ):
            divergencias.append((estado, nota, py, tuple(linha)))
    assert divergencias == []
