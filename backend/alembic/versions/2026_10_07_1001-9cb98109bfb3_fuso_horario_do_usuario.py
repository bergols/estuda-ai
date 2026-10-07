"""usuarios.fuso_horario: "hoje" é calculado no fuso de cada usuário

ADD COLUMN com DEFAULT constante é instantâneo desde o Postgres 11: o valor
padrão fica guardado no catálogo e as linhas antigas não são reescritas.

Validação: o nome precisa ser um fuso que o Postgres conhece (pg_timezone_names).
Um CHECK não pode fazer subconsulta, e a lista de fusos vem do pacote tzdata,
que muda com atualizações: um CHECK deve depender só da própria linha, sempre
com o mesmo resultado. Por isso a validação é um TRIGGER BEFORE INSERT/UPDATE
que tenta usar o fuso (now() AT TIME ZONE ...). Fuso desconhecido = erro
22023 (invalid_parameter_value), que a API transforma em 422.

Revision ID: 9cb98109bfb3
Revises: 3e02914d3c6e
Create Date: 2026-10-07 10:01:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9cb98109bfb3"
down_revision: Union[str, Sequence[str], None] = "3e02914d3c6e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "usuarios",
        sa.Column("fuso_horario", sa.Text(), nullable=False, server_default="America/Sao_Paulo"),
    )
    op.execute(
        """
        CREATE FUNCTION validar_fuso_horario() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM now() AT TIME ZONE NEW.fuso_horario;  -- erro 22023 se o fuso não existe
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_usuarios_fuso_valido BEFORE INSERT OR UPDATE OF fuso_horario ON usuarios
        FOR EACH ROW EXECUTE FUNCTION validar_fuso_horario()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_usuarios_fuso_valido ON usuarios")
    op.execute("DROP FUNCTION validar_fuso_horario()")
    op.drop_column("usuarios", "fuso_horario")
