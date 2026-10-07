"""SM-2 (SuperMemo 2, Piotr Woźniak, 1987): o algoritmo de repetição espaçada.

Após cada revisão, o aluno dá uma nota de 0 a 5:
    5 lembrou perfeitamente      2 errou, mas a resposta pareceu fácil ao ver
    4 lembrou após hesitar       1 errou; lembrou ao ver a resposta
    3 lembrou com dificuldade    0 branco total

Estado de um card: facilidade (EF, mínimo 1,3), intervalo (dias) e repetições
seguidas com sucesso.
- nota >= 3 (lembrou): intervalo 1 dia, depois 6, depois intervalo_anterior * EF;
  repetições + 1.
- nota < 3 (esqueceu): volta ao começo (repetições 0, intervalo 1).
- EF' = EF + (0,1 - (5 - nota) * (0,08 + (5 - nota) * 0,02)), nunca abaixo de 1,3.
  Nota 5 soma 0,1; nota 4 não muda; nota 3 tira 0,14; nota 0 tira 0,8.

Esta é a versão usada pela API. A mesma conta existe em PL/pgSQL (função sm2(),
migration "funcao_sm2_plpgsql") e os testes conferem que as duas concordam.
Arredondamento: Decimal com ROUND_HALF_UP, igual ao round(numeric) do Postgres.
O round() nativo do Python arredonda o meio para o PAR (round(2.5) == 2) e daria
intervalos diferentes dos do banco.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

FACILIDADE_INICIAL = Decimal("2.50")
FACILIDADE_MINIMA = Decimal("1.30")


@dataclass(frozen=True)
class EstadoSM2:
    facilidade: Decimal = FACILIDADE_INICIAL
    intervalo: int = 0
    repeticoes: int = 0


def calcular(estado: EstadoSM2, nota: int) -> EstadoSM2:
    if not 0 <= nota <= 5:
        raise ValueError(f"nota deve estar entre 0 e 5 (recebida: {nota})")

    if nota >= 3:
        if estado.repeticoes == 0:
            intervalo = 1
        elif estado.repeticoes == 1:
            intervalo = 6
        else:
            intervalo = int(
                (estado.intervalo * estado.facilidade).quantize(Decimal(1), ROUND_HALF_UP)
            )
        repeticoes = estado.repeticoes + 1
    else:
        intervalo, repeticoes = 1, 0

    erro = 5 - nota
    delta = Decimal("0.1") - erro * (Decimal("0.08") + erro * Decimal("0.02"))
    facilidade = max(
        FACILIDADE_MINIMA, (estado.facilidade + delta).quantize(Decimal("0.01"), ROUND_HALF_UP)
    )
    return EstadoSM2(facilidade, intervalo, repeticoes)


def proxima_revisao(revisado_em: datetime, intervalo_dias: int) -> datetime:
    """Instante da próxima revisão. timedelta(days=n) = n × 24 h exatas.

    (No Postgres, timestamptz + interval '1 day' soma um dia de CALENDÁRIO no fuso
    da sessão, que pode ter 23 ou 25 h na troca de horário de verão. Fazendo a
    conta aqui, em instantes UTC, o resultado não depende do fuso da sessão.)
    """
    return revisado_em + timedelta(days=intervalo_dias)
