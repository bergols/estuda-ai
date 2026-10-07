"""função sm2() em PL/pgSQL (experimento didático)

A mesma conta do SM-2 de app/servicos/sm2.py, escrita no banco. A API usa a
versão em Python; esta existe para comparar as duas abordagens (prós e contras
em docs/repeticao-espacada.md) e os testes conferem que dão o mesmo resultado.

IMMUTABLE: mesma entrada, mesma saída, sem ler tabelas. Permite que o Postgres
avalie a chamada uma vez só quando os argumentos são constantes, e que a função
seja usada em índices de expressão e colunas geradas.
STRICT: se qualquer argumento for NULL, devolve NULL sem executar o corpo.

Arredondamento: round(numeric) no Postgres arredonda o meio "para longe do zero"
(2,5 -> 3). O round() do Python arredonda o meio para o PAR (2,5 -> 2); a versão
Python usa Decimal com ROUND_HALF_UP para dar o mesmo resultado.

Revision ID: f1d2970537cb
Revises: 9cb98109bfb3
Create Date: 2026-10-07 10:02:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "f1d2970537cb"
down_revision: Union[str, Sequence[str], None] = "9cb98109bfb3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION sm2(
            p_facilidade numeric, p_intervalo integer, p_repeticoes integer, p_nota integer,
            OUT facilidade numeric, OUT intervalo integer, OUT repeticoes integer
        )
        LANGUAGE plpgsql IMMUTABLE STRICT AS $$
        BEGIN
            IF p_nota NOT BETWEEN 0 AND 5 THEN
                RAISE EXCEPTION 'nota deve estar entre 0 e 5 (recebida: %)', p_nota
                    USING ERRCODE = 'check_violation';
            END IF;

            IF p_nota >= 3 THEN                      -- lembrou
                IF p_repeticoes = 0 THEN
                    intervalo := 1;
                ELSIF p_repeticoes = 1 THEN
                    intervalo := 6;
                ELSE
                    intervalo := round(p_intervalo * p_facilidade)::integer;
                END IF;
                repeticoes := p_repeticoes + 1;
            ELSE                                     -- esqueceu: recomeça
                repeticoes := 0;
                intervalo := 1;
            END IF;

            -- EF' = EF + (0,1 - (5 - q) * (0,08 + (5 - q) * 0,02)), mínimo 1,3
            facilidade := greatest(
                1.30,
                round(p_facilidade + (0.1 - (5 - p_nota) * (0.08 + (5 - p_nota) * 0.02)), 2)
            );
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION sm2(numeric, integer, integer, integer)")
