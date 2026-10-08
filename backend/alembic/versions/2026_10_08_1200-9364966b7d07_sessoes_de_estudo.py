"""sessoes_estudo, pausas_sessao, eventos_foco e vw_sessoes_foco (fase 7)

Sessões de estudo do app desktop (pomodoro, bloco contínuo, 52/17, personalizado),
com as pausas e os eventos de foco (saída da janela, tentativa de abrir programa ou
site bloqueado, saída de emergência).

O ponto central é a CHAVE DE IDEMPOTÊNCIA. O app desktop grava tudo num SQLite local e
manda ao servidor quando consegue. Se a resposta se perde (timeout, Wi-Fi caiu no meio),
o app não sabe se o servidor gravou e manda de novo. Sem proteção, a mesma sessão
entraria duas vezes, e o gráfico de horas de foco dobraria. Por isso:

- cada sessão, pausa e evento nasce no APP com um UUID (`chave`), antes de existir rede;
- o banco garante unicidade: UNIQUE (usuario_id, chave) nas sessões e
  UNIQUE (sessao_id, chave) nas pausas e eventos;
- o servidor grava com INSERT ... ON CONFLICT: reenviar o mesmo lote é inofensivo.

A chave é do app (e não o id do banco) porque o id só existe DEPOIS do primeiro envio
bem-sucedido, e o problema é justamente não saber se ele aconteceu. E não é
(usuario_id, iniciada_em): dois computadores podem começar sessões no mesmo segundo, e
o relógio do computador pode ser corrigido entre um envio e outro.

Outras decisões:
- Tempos do método em colunas (foco_min, pausa_min, ciclos...), não em jsonb: são
  poucos, conhecidos, e precisam de CHECK ("52/17 é 52 e 17"). jsonb é para estrutura
  realmente aberta.
- duracao_planejada_s e duracao_real_s são COLUNAS GERADAS (STORED): o banco calcula a
  partir das outras colunas e ninguém consegue gravar um valor incoerente.
- O foco efetivo (real − pausas − tempo fora da janela) NÃO é coluna: depende de linhas
  de outras tabelas. Fica na view vw_sessoes_foco, calculado dos fatos.
- Pausas e eventos são só-INSERT (privilégios SELECT, INSERT), como historico_revisoes:
  chegam como fatos completos (a pausa só é enviada quando termina).
- disciplina_id com FK composta (disciplina_id, usuario_id), como em geracoes: o banco
  impede ligar a sessão à disciplina de outro usuário. Apagar a disciplina anula só
  essa coluna (SET NULL (disciplina_id)): as horas estudadas continuam no histórico.
- ocorrido_em/iniciada_em vêm do relógio do APP (é quando aconteceu); recebido_em/
  criado_em, do relógio do servidor. A diferença mostra o atraso da sincronização.

Revision ID: 9364966b7d07
Revises: 5d43d1f1af32
Create Date: 2026-10-08 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9364966b7d07"
down_revision: Union[str, Sequence[str], None] = "5d43d1f1af32"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

APP = "estuda_ai_app"


def _agora():
    return sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "sessoes_estudo",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("usuario_id", sa.BigInteger(), nullable=False),
        sa.Column("disciplina_id", sa.BigInteger(), nullable=True),
        sa.Column("chave", sa.Uuid(), nullable=False),
        sa.Column("metodo", sa.Text(), nullable=False),
        sa.Column("foco_min", sa.SmallInteger(), nullable=False),
        sa.Column("pausa_min", sa.SmallInteger(), nullable=False),
        sa.Column("ciclos", sa.SmallInteger(), nullable=False),
        sa.Column("pausa_longa_min", sa.SmallInteger(), nullable=True),
        sa.Column("ciclos_ate_pausa_longa", sa.SmallInteger(), nullable=True),
        sa.Column("meta", sa.Text(), nullable=True),
        sa.Column("sistema", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("iniciada_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminada_em", sa.DateTime(timezone=True), nullable=True),
        # Tempo de FOCO planejado (sem as pausas): 4 pomodoros de 25 min = 6000 s
        sa.Column(
            "duracao_planejada_s",
            sa.Integer(),
            sa.Computed("foco_min * 60 * ciclos", persisted=True),
            nullable=False,
        ),
        # Do início ao fim, com pausas. NULL enquanto a sessão está em andamento.
        sa.Column(
            "duracao_real_s",
            sa.Integer(),
            sa.Computed(
                "(extract(epoch FROM terminada_em - iniciada_em))::integer", persisted=True
            ),
            nullable=True,
        ),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=_agora(), nullable=False),
        sa.Column(
            "atualizado_em", sa.DateTime(timezone=True), server_default=_agora(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessoes_estudo")),
        sa.ForeignKeyConstraint(
            ["usuario_id"],
            ["usuarios.id"],
            name=op.f("fk_sessoes_estudo_usuario_id_usuarios"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["disciplina_id", "usuario_id"],
            ["disciplinas.id", "disciplinas.usuario_id"],
            name=op.f("fk_sessoes_estudo_disciplina_id_disciplinas"),
            ondelete="SET NULL (disciplina_id)",
        ),
        # A chave de idempotência. usuario_id na frente: (1) a mesma chave vinda de outro
        # usuário não colide nem "acha" a sessão alheia; (2) o índice também atende a FK
        # usuario_id e a busca "sessões do usuário X".
        sa.UniqueConstraint("usuario_id", "chave", name=op.f("uq_sessoes_estudo_usuario_id_chave")),
        sa.CheckConstraint(
            "metodo IN ('pomodoro', 'bloco', '52_17', 'personalizado')",
            name=op.f("ck_sessoes_estudo_metodo_valido"),
        ),
        sa.CheckConstraint(
            "foco_min BETWEEN 1 AND 240", name=op.f("ck_sessoes_estudo_foco_min_valido")
        ),
        sa.CheckConstraint(
            "pausa_min BETWEEN 0 AND 60", name=op.f("ck_sessoes_estudo_pausa_min_valida")
        ),
        sa.CheckConstraint("ciclos BETWEEN 1 AND 12", name=op.f("ck_sessoes_estudo_ciclos_validos")),
        sa.CheckConstraint(
            "pausa_longa_min BETWEEN 1 AND 90",
            name=op.f("ck_sessoes_estudo_pausa_longa_valida"),
        ),
        sa.CheckConstraint(
            "ciclos_ate_pausa_longa BETWEEN 2 AND 12",
            name=op.f("ck_sessoes_estudo_ciclos_ate_pausa_longa_validos"),
        ),
        # As duas colunas da pausa longa existem juntas ou não existem
        sa.CheckConstraint(
            "(pausa_longa_min IS NULL) = (ciclos_ate_pausa_longa IS NULL)",
            name=op.f("ck_sessoes_estudo_pausa_longa_completa"),
        ),
        # Regras de cada método, no banco ("se A então B" = "NOT A OR B")
        sa.CheckConstraint(
            "metodo <> '52_17' OR (foco_min = 52 AND pausa_min = 17)",
            name=op.f("ck_sessoes_estudo_metodo_52_17"),
        ),
        sa.CheckConstraint(
            "metodo <> 'bloco' OR (pausa_min = 0 AND ciclos = 1 AND pausa_longa_min IS NULL)",
            name=op.f("ck_sessoes_estudo_metodo_bloco"),
        ),
        sa.CheckConstraint(
            "meta IS NULL OR length(trim(meta)) BETWEEN 1 AND 200",
            name=op.f("ck_sessoes_estudo_meta_valida"),
        ),
        sa.CheckConstraint(
            "sistema IN ('windows', 'macos', 'linux', 'web')",
            name=op.f("ck_sessoes_estudo_sistema_valido"),
        ),
        sa.CheckConstraint(
            "status IN ('em_andamento', 'concluida', 'abandonada')",
            name=op.f("ck_sessoes_estudo_status_valido"),
        ),
        # Terminada se e somente se tem fim
        sa.CheckConstraint(
            "(status = 'em_andamento') = (terminada_em IS NULL)",
            name=op.f("ck_sessoes_estudo_fim_conforme_status"),
        ),
        sa.CheckConstraint(
            "terminada_em >= iniciada_em", name=op.f("ck_sessoes_estudo_fim_apos_inicio")
        ),
    )
    # Analytics por período: "sessões do usuário entre X e Y" (horas por dia/semana)
    op.create_index(
        "ix_sessoes_estudo_usuario_iniciada_em", "sessoes_estudo", ["usuario_id", "iniciada_em"]
    )
    # FK opcional: índice parcial (sessão sem disciplina não precisa entrar no índice)
    op.create_index(
        "ix_sessoes_estudo_disciplina_id",
        "sessoes_estudo",
        ["disciplina_id"],
        postgresql_where=sa.text("disciplina_id IS NOT NULL"),
    )
    op.execute(
        """
        CREATE TRIGGER trg_sessoes_estudo_atualizado_em
        BEFORE UPDATE ON sessoes_estudo
        FOR EACH ROW EXECUTE FUNCTION definir_atualizado_em()
        """
    )

    op.create_table(
        "pausas_sessao",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("sessao_id", sa.BigInteger(), nullable=False),
        sa.Column("chave", sa.Uuid(), nullable=False),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("iniciada_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminada_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "duracao_s",
            sa.Integer(),
            sa.Computed(
                "(extract(epoch FROM terminada_em - iniciada_em))::integer", persisted=True
            ),
            nullable=False,
        ),
        sa.Column("recebido_em", sa.DateTime(timezone=True), server_default=_agora(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pausas_sessao")),
        sa.ForeignKeyConstraint(
            ["sessao_id"],
            ["sessoes_estudo.id"],
            name=op.f("fk_pausas_sessao_sessao_id_sessoes_estudo"),
            ondelete="CASCADE",
        ),
        # Idempotência + índice da FK (sessao_id é a 1ª coluna)
        sa.UniqueConstraint("sessao_id", "chave", name=op.f("uq_pausas_sessao_sessao_id_chave")),
        sa.CheckConstraint(
            "tipo IN ('curta', 'longa', 'manual')", name=op.f("ck_pausas_sessao_tipo_valido")
        ),
        sa.CheckConstraint(
            "terminada_em >= iniciada_em", name=op.f("ck_pausas_sessao_fim_apos_inicio")
        ),
    )

    op.create_table(
        "eventos_foco",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("sessao_id", sa.BigInteger(), nullable=False),
        sa.Column("chave", sa.Uuid(), nullable=False),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("ocorrido_em", sa.DateTime(timezone=True), nullable=False),
        # Só na saída da janela: quanto tempo ficou fora
        sa.Column("duracao_s", sa.Integer(), nullable=True),
        # Nome do programa ou site, quando houver (ex.: "Discord.exe", "youtube.com")
        sa.Column("detalhe", sa.Text(), nullable=True),
        sa.Column("recebido_em", sa.DateTime(timezone=True), server_default=_agora(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eventos_foco")),
        sa.ForeignKeyConstraint(
            ["sessao_id"],
            ["sessoes_estudo.id"],
            name=op.f("fk_eventos_foco_sessao_id_sessoes_estudo"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("sessao_id", "chave", name=op.f("uq_eventos_foco_sessao_id_chave")),
        sa.CheckConstraint(
            "tipo IN ('saida_janela', 'programa_bloqueado', 'site_bloqueado', 'saida_emergencia')",
            name=op.f("ck_eventos_foco_tipo_valido"),
        ),
        sa.CheckConstraint(
            "(tipo = 'saida_janela') = (duracao_s IS NOT NULL)",
            name=op.f("ck_eventos_foco_duracao_so_na_saida"),
        ),
        sa.CheckConstraint("duracao_s >= 0", name=op.f("ck_eventos_foco_duracao_nao_negativa")),
        sa.CheckConstraint(
            "detalhe IS NULL OR length(detalhe) BETWEEN 1 AND 200",
            name=op.f("ck_eventos_foco_detalhe_valido"),
        ),
    )

    # Uma linha por sessão com os números derivados. LATERAL: para cada sessão, uma
    # subconsulta que soma SÓ as pausas/eventos dela (usa os índices únicos, que começam
    # por sessao_id). Sessão em andamento tem duração real e foco efetivo NULL.
    op.execute(
        """
        CREATE VIEW vw_sessoes_foco AS
        SELECT s.id,
               s.usuario_id,
               s.disciplina_id,
               s.metodo,
               s.status,
               s.sistema,
               s.iniciada_em,
               s.terminada_em,
               (s.iniciada_em AT TIME ZONE u.fuso_horario)::date AS dia,
               s.duracao_planejada_s,
               s.duracao_real_s,
               p.pausas_s,
               e.fora_s,
               e.interrupcoes,
               e.emergencias,
               -- GREATEST do Postgres IGNORA NULL: GREATEST(NULL, 0) = 0, e uma sessão em
               -- andamento apareceria com "0 de foco". O CASE devolve NULL ("ainda não
               -- se sabe"), que AVG/SUM pulam. (O teste da view pegou isso.)
               CASE WHEN s.duracao_real_s IS NOT NULL
                    THEN GREATEST(s.duracao_real_s - p.pausas_s - e.fora_s, 0)
               END AS foco_efetivo_s
        FROM sessoes_estudo AS s
        JOIN usuarios AS u ON u.id = s.usuario_id
        CROSS JOIN LATERAL (
            SELECT COALESCE(sum(ps.duracao_s), 0)::integer AS pausas_s
            FROM pausas_sessao AS ps
            WHERE ps.sessao_id = s.id
        ) AS p
        CROSS JOIN LATERAL (
            SELECT COALESCE(sum(ef.duracao_s), 0)::integer AS fora_s,
                   count(*) FILTER (WHERE ef.tipo <> 'saida_emergencia')::integer AS interrupcoes,
                   count(*) FILTER (WHERE ef.tipo = 'saida_emergencia')::integer AS emergencias
            FROM eventos_foco AS ef
            WHERE ef.sessao_id = s.id
        ) AS e
        """
    )

    op.execute(f"GRANT SELECT, INSERT, UPDATE ON sessoes_estudo TO {APP}")
    op.execute(f"GRANT SELECT, INSERT ON pausas_sessao TO {APP}")
    op.execute(f"GRANT SELECT, INSERT ON eventos_foco TO {APP}")
    op.execute(f"GRANT SELECT ON vw_sessoes_foco TO {APP}")


def downgrade() -> None:
    op.execute("DROP VIEW vw_sessoes_foco")
    op.drop_table("eventos_foco")
    op.drop_table("pausas_sessao")
    op.drop_table("sessoes_estudo")
