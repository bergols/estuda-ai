"""spotify_contas, spotify_playlists e preferencias_foco (fase 7, Spotify)

spotify_contas (1:1 com usuarios, PK = FK): a conexão com o Spotify.
- refresh_token e access_token em bytea CIFRADOS NA APLICAÇÃO (AES-256-GCM, chave no
  .env; ver app/servicos/cifra.py). Por que não pgcrypto: a chave viajaria dentro do
  SQL (logs, pg_stat_statements, mensagens de erro) e passaria pelo servidor do banco.
  O Postgres só guarda bytes opacos; o 1o byte é a versão da chave (rotação).
- O access token (vale 1 h) também fica guardado, com a validade: os dois computadores
  usam o mesmo enquanto valer, em vez de cada um renovar o seu. Renovar com
  SELECT ... FOR UPDATE nesta linha: o Spotify pode TROCAR o refresh token a cada
  renovação, e duas renovações simultâneas fariam uma invalidar a outra.
- Desconectar = DELETE da linha (privilégio DELETE só aqui, entre as tabelas novas).

spotify_playlists: que playlist tocar. alvo = 'padrao' | 'metodo' | 'disciplina' |
'intervalo'. Na hora de tocar: a da disciplina, senão a do método, senão a padrão.
- CHECK "(alvo = 'metodo') = (metodo IS NOT NULL)": a coluna existe só para o alvo dela.
- UNIQUE NULLS NOT DISTINCT (usuario_id, alvo, metodo, disciplina_id): no UNIQUE comum,
  NULL é sempre "diferente" de NULL, então daria para ter DUAS playlists 'padrao' (as
  duas com metodo e disciplina_id NULL). NULLS NOT DISTINCT (Postgres 15+) trata NULLs
  como iguais nessa comparação. Sem isso, a saída clássica era um índice único sobre
  coalesce(metodo, '') e coalesce(disciplina_id, 0).
- FK composta (disciplina_id, usuario_id) com CASCADE: a playlist de uma disciplina
  some com ela, e não dá para apontar para a disciplina de outra pessoa.
- Independente de spotify_contas: desconectar e reconectar não perde a configuração.

preferencias_foco (1:1 com usuarios): o que fazer com a música no intervalo
('pausar' | 'trocar' para a playlist de intervalo | 'continuar'). A sessão 4 da fase
acrescenta a espera da saída de emergência, em migration nova.

Revision ID: 9fb7abed3cf1
Revises: f7c26c04528a
Create Date: 2026-10-09 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9fb7abed3cf1"
down_revision: Union[str, Sequence[str], None] = "f7c26c04528a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

APP = "estuda_ai_app"
URI_SPOTIFY = r"^spotify:(playlist|album|artist):[A-Za-z0-9]{22}$"


def _agora():
    return sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "spotify_contas",
        sa.Column("usuario_id", sa.BigInteger(), nullable=False),
        sa.Column("spotify_id", sa.Text(), nullable=False),
        sa.Column("nome", sa.Text(), nullable=True),
        sa.Column("escopos", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.LargeBinary(), nullable=False),
        sa.Column("access_token", sa.LargeBinary(), nullable=True),
        sa.Column("access_expira_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("conectado_em", sa.DateTime(timezone=True), server_default=_agora(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=_agora(), nullable=False),
        sa.PrimaryKeyConstraint("usuario_id", name=op.f("pk_spotify_contas")),
        sa.ForeignKeyConstraint(
            ["usuario_id"], ["usuarios.id"],
            name=op.f("fk_spotify_contas_usuario_id_usuarios"), ondelete="CASCADE",
        ),
        # 1 byte de versão + 12 de nonce + 16 de tag: menos que isso não é um valor cifrado
        sa.CheckConstraint(
            "octet_length(refresh_token) > 29 AND get_byte(refresh_token, 0) BETWEEN 1 AND 255",
            name=op.f("ck_spotify_contas_refresh_cifrado"),
        ),
        sa.CheckConstraint(
            "access_token IS NULL OR octet_length(access_token) > 29",
            name=op.f("ck_spotify_contas_access_cifrado"),
        ),
        sa.CheckConstraint(
            "(access_token IS NULL) = (access_expira_em IS NULL)",
            name=op.f("ck_spotify_contas_access_com_validade"),
        ),
    )
    op.execute(
        """
        CREATE TRIGGER trg_spotify_contas_atualizado_em
        BEFORE UPDATE ON spotify_contas
        FOR EACH ROW EXECUTE FUNCTION definir_atualizado_em()
        """
    )

    op.create_table(
        "spotify_playlists",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("usuario_id", sa.BigInteger(), nullable=False),
        sa.Column("alvo", sa.Text(), nullable=False),
        sa.Column("metodo", sa.Text(), nullable=True),
        sa.Column("disciplina_id", sa.BigInteger(), nullable=True),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=_agora(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_spotify_playlists")),
        sa.ForeignKeyConstraint(
            ["usuario_id"], ["usuarios.id"],
            name=op.f("fk_spotify_playlists_usuario_id_usuarios"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["disciplina_id", "usuario_id"], ["disciplinas.id", "disciplinas.usuario_id"],
            name=op.f("fk_spotify_playlists_disciplina_id_disciplinas"), ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "usuario_id", "alvo", "metodo", "disciplina_id",
            name=op.f("uq_spotify_playlists_usuario_id_alvo_metodo_disciplina_id"),
            postgresql_nulls_not_distinct=True,
        ),
        sa.CheckConstraint(
            "alvo IN ('padrao', 'metodo', 'disciplina', 'intervalo')",
            name=op.f("ck_spotify_playlists_alvo_valido"),
        ),
        sa.CheckConstraint(
            "metodo IN ('pomodoro', 'bloco', '52_17', 'personalizado')",
            name=op.f("ck_spotify_playlists_metodo_valido"),
        ),
        sa.CheckConstraint(
            "(alvo = 'metodo') = (metodo IS NOT NULL)",
            name=op.f("ck_spotify_playlists_metodo_so_no_alvo_metodo"),
        ),
        sa.CheckConstraint(
            "(alvo = 'disciplina') = (disciplina_id IS NOT NULL)",
            name=op.f("ck_spotify_playlists_disciplina_so_no_alvo_disciplina"),
        ),
        sa.CheckConstraint(f"uri ~ '{URI_SPOTIFY}'", name=op.f("ck_spotify_playlists_uri_valida")),
        sa.CheckConstraint(
            "length(trim(nome)) BETWEEN 1 AND 200", name=op.f("ck_spotify_playlists_nome_valido")
        ),
    )
    op.create_index(
        "ix_spotify_playlists_disciplina_id", "spotify_playlists", ["disciplina_id"],
        postgresql_where=sa.text("disciplina_id IS NOT NULL"),
    )

    op.create_table(
        "preferencias_foco",
        sa.Column("usuario_id", sa.BigInteger(), nullable=False),
        sa.Column("spotify_no_intervalo", sa.Text(), server_default="pausar", nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=_agora(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=_agora(), nullable=False),
        sa.PrimaryKeyConstraint("usuario_id", name=op.f("pk_preferencias_foco")),
        sa.ForeignKeyConstraint(
            ["usuario_id"], ["usuarios.id"],
            name=op.f("fk_preferencias_foco_usuario_id_usuarios"), ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "spotify_no_intervalo IN ('pausar', 'trocar', 'continuar')",
            name=op.f("ck_preferencias_foco_spotify_no_intervalo_valido"),
        ),
    )
    op.execute(
        """
        CREATE TRIGGER trg_preferencias_foco_atualizado_em
        BEFORE UPDATE ON preferencias_foco
        FOR EACH ROW EXECUTE FUNCTION definir_atualizado_em()
        """
    )

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON spotify_contas TO {APP}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON spotify_playlists TO {APP}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON preferencias_foco TO {APP}")


def downgrade() -> None:
    op.drop_table("preferencias_foco")
    op.drop_table("spotify_playlists")
    op.drop_table("spotify_contas")
