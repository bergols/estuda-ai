"""sites_bloqueados, programas_bloqueados e preferências de bloqueio (fase 7, sessão 4)

O que o modo foco bloqueia fica no banco para valer nos dois computadores: os sites
são os mesmos em qualquer sistema; os programas, não (no Windows é "Discord.exe", no
Mac é "Discord"), então cada programa tem o sistema dele.

sites_bloqueados:
- `dominio` validado por CHECK com expressão regular: só letras minúsculas, dígitos,
  hífen e pontos, com um TLD de letras ("youtube.com", "m.facebook.com"). O valor vai
  parar no arquivo hosts, escrito por um processo de ADMINISTRADOR: uma quebra de linha
  ou um espaço dentro do domínio viraria uma linha nova no hosts (redirecionar o banco
  para outro IP, por exemplo). A API valida, o app valida de novo, e o banco é a última
  barreira, que vale para qualquer caminho de escrita (inclusive um script de admin).
- Sem "www.": o app bloqueia o domínio e o www dele juntos.

programas_bloqueados:
- UNIQUE sobre uma EXPRESSÃO: `(usuario_id, sistema, lower(nome))`. "Discord" e
  "discord" são o mesmo programa (a comparação na hora de fechar é sem diferença de
  maiúsculas), mas o nome fica guardado como foi digitado. Uma UNIQUE constraint só
  aceita colunas; com expressão, é um índice único (CREATE UNIQUE INDEX), que garante
  a mesma coisa (o mesmo recurso de uq_disciplinas_usuario_nome). A alternativa seria a extensão citext, um tipo inteiro só para isso.
- `nome` sem "/", "\\" nem caracteres de controle: é o nome do programa, não um caminho.

As duas listas são trocadas inteiras pela tela (PUT), sem UPDATE: uma linha nunca muda,
ou existe ou não. Por isso o app só recebe SELECT, INSERT e DELETE nelas. A troca é um
comando só, com CTE que modifica dados (app/servicos/bloqueios.py).

preferencias_foco ganha:
- espera_emergencia_s (10 a 600, padrão 60): quanto a saída de emergência espera antes
  de liberar. É atrito de propósito: tempo para o impulso passar.
- bloquear_sites / bloquear_programas: se a sessão bloqueia (o padrão é não: bloquear
  sites pede a senha do computador, e isso tem que ser escolha sua).

Revision ID: de93fdad2986
Revises: 9fb7abed3cf1
Create Date: 2026-10-09 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "de93fdad2986"
down_revision: Union[str, Sequence[str], None] = "9fb7abed3cf1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

APP = "estuda_ai_app"
# Rótulos de 1 a 63 caracteres (letra/dígito nas pontas, hífen no meio), TLD só de letras
DOMINIO = r"^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"


def _agora():
    return sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "sites_bloqueados",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("usuario_id", sa.BigInteger(), nullable=False),
        sa.Column("dominio", sa.Text(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=_agora(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sites_bloqueados")),
        sa.ForeignKeyConstraint(
            ["usuario_id"], ["usuarios.id"],
            name=op.f("fk_sites_bloqueados_usuario_id_usuarios"), ondelete="CASCADE",
        ),
        # A FK usuario_id fica coberta: é a 1a coluna deste UNIQUE
        sa.UniqueConstraint("usuario_id", "dominio", name=op.f("uq_sites_bloqueados_usuario_id_dominio")),
        sa.CheckConstraint(
            f"dominio ~ '{DOMINIO}' AND length(dominio) <= 253",
            name=op.f("ck_sites_bloqueados_dominio_valido"),
        ),
    )

    op.create_table(
        "programas_bloqueados",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("usuario_id", sa.BigInteger(), nullable=False),
        sa.Column("sistema", sa.Text(), nullable=False),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=_agora(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_programas_bloqueados")),
        sa.ForeignKeyConstraint(
            ["usuario_id"], ["usuarios.id"],
            name=op.f("fk_programas_bloqueados_usuario_id_usuarios"), ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "sistema IN ('macos', 'windows')", name=op.f("ck_programas_bloqueados_sistema_valido")
        ),
        sa.CheckConstraint(
            r"length(nome) BETWEEN 1 AND 100 AND nome = trim(nome) AND nome !~ '[/\\[:cntrl:]]'",
            name=op.f("ck_programas_bloqueados_nome_valido"),
        ),
    )
    # Índice único sobre expressão (ver o topo). Também cobre a FK usuario_id.
    op.execute(
        "CREATE UNIQUE INDEX uq_programas_bloqueados_usuario_id_sistema_nome "
        "ON programas_bloqueados (usuario_id, sistema, lower(nome))"
    )

    op.add_column(
        "preferencias_foco",
        sa.Column("espera_emergencia_s", sa.Integer(), server_default="60", nullable=False),
    )
    op.add_column(
        "preferencias_foco",
        sa.Column("bloquear_sites", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "preferencias_foco",
        sa.Column("bloquear_programas", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_preferencias_foco_espera_emergencia_valida"),
        "preferencias_foco",
        "espera_emergencia_s BETWEEN 10 AND 600",
    )

    op.execute(f"GRANT SELECT, INSERT, DELETE ON sites_bloqueados TO {APP}")
    op.execute(f"GRANT SELECT, INSERT, DELETE ON programas_bloqueados TO {APP}")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_preferencias_foco_espera_emergencia_valida"), "preferencias_foco")
    op.drop_column("preferencias_foco", "bloquear_programas")
    op.drop_column("preferencias_foco", "bloquear_sites")
    op.drop_column("preferencias_foco", "espera_emergencia_s")
    op.drop_table("programas_bloqueados")
    op.drop_table("sites_bloqueados")
