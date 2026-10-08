"""papel estuda_ai_app: a aplicação conecta com o MÍNIMO de privilégios

PRINCÍPIO DO MENOR PRIVILÉGIO: cada parte do sistema recebe só as permissões de
que precisa para o seu trabalho. Se a API for comprometida (um bug, uma SQL
injection que escapou, uma dependência maliciosa), o estrago possível é limitado
ao que o papel dela pode fazer: com este papel, ninguém apaga uma tabela, altera o
schema, zera uma tabela com TRUNCATE ou adultera a auditoria de gastos.

Dois papéis:
- o DONO (o usuário das migrations, MIGRATION_DATABASE_URL): cria e altera o
  schema. Só as migrations e os scripts de admin usam;
- estuda_ai_app (DATABASE_URL): só lê e escreve DADOS, tabela por tabela.

Detalhes que este desenho usa (testados num banco de laboratório):
- inserir em coluna GENERATED ... AS IDENTITY não exige permissão na sequência;
- as ações de FK (ON DELETE CASCADE / SET NULL) rodam com os privilégios do DONO
  da tabela. Por isso histórico, tentativas e auditoria recebem só SELECT e INSERT:
  a imutabilidade fica garantida também por PRIVILÉGIO, e apagar uma disciplina
  ainda apaga o histórico dela em cascata;
- sem DEFAULT PRIVILEGES: toda tabela nova precisa de um GRANT explícito na sua
  migration (tests/test_privilegios.py falha se alguma tabela ficar sem decisão).

REFRESH MATERIALIZED VIEW exige ser DONO da MV (no Postgres 16; o 17 criou o
privilégio MAINTAIN). A saída é uma função SECURITY DEFINER: ela roda com os
privilégios de quem a CRIOU (o dono), e o app só recebe EXECUTE nela. Cuidados
obrigatórios com SECURITY DEFINER:
- SET search_path = pg_catalog, public, pg_temp: senão quem chama poderia criar, num
  schema que vem antes no search_path, uma tabela ou função com o mesmo nome e fazer
  o código privilegiado usá-la. O pg_temp vai por ÚLTIMO de propósito: se não for
  listado, o Postgres procura tabelas nele ANTES de todos (e qualquer papel pode criar
  tabelas temporárias). Além disso, a função qualifica os nomes (public.<tabela>);
- REVOKE EXECUTE ... FROM PUBLIC: por padrão TODO papel pode executar funções.

O papel é criado aqui SEM login e sem senha (a migration não conhece segredos).
scripts/papel_app.py dá LOGIN e a senha que está em DATABASE_URL. Papéis são do
SERVIDOR inteiro, não de um banco: o downgrade tira os privilégios deste banco,
mas não apaga o papel (ele pode ter privilégios em outros bancos, ex.: o de testes).

Revision ID: 5d43d1f1af32
Revises: 9a4980aa8776
Create Date: 2026-10-08 11:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "5d43d1f1af32"
down_revision: Union[str, Sequence[str], None] = "9a4980aa8776"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

APP = "estuda_ai_app"

# tabela -> privilégios do app. Toda tabela do schema precisa estar aqui.
PRIVILEGIOS = {
    "usuarios": "SELECT, INSERT, UPDATE",  # sem DELETE: o app nunca apaga contas
    "disciplinas": "SELECT, INSERT, UPDATE, DELETE",
    "materiais": "SELECT, INSERT, UPDATE, DELETE",
    "trechos": "SELECT, INSERT, UPDATE, DELETE",
    "flashcards": "SELECT, INSERT, UPDATE, DELETE",
    "flashcard_trechos": "SELECT, INSERT, UPDATE, DELETE",
    "questoes": "SELECT, INSERT, UPDATE, DELETE",
    "questao_trechos": "SELECT, INSERT, UPDATE, DELETE",
    "alternativas": "SELECT, INSERT, UPDATE, DELETE",
    "revisoes": "SELECT, INSERT, UPDATE, DELETE",
    "limites_taxa": "SELECT, INSERT, UPDATE, DELETE",
    # só INSERT: histórico, respostas e auditoria de gasto não se alteram nem se apagam
    # (a cascata de FK continua apagando, porque roda como dono)
    "historico_revisoes": "SELECT, INSERT",
    "tentativas": "SELECT, INSERT",
    "geracoes": "SELECT, INSERT",
    # só leitura (a MV e seu registro são atualizados pela função SECURITY DEFINER)
    "vw_respostas": "SELECT",
    "mv_respostas_diarias": "SELECT",
    "atualizacoes_mv": "SELECT",
    # alembic_version: nenhum (o app não precisa saber a versão do schema)
}


def upgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP}') THEN
                CREATE ROLE {APP} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
            END IF;
        END
        $$
        """
    )
    # Desde o PG 15 o PUBLIC já não pode criar objetos em "public"; deixado explícito.
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP}")
    for tabela, privilegios in PRIVILEGIOS.items():
        op.execute(f"GRANT {privilegios} ON {tabela} TO {APP}")

    op.execute(
        """
        CREATE FUNCTION atualizar_mv_respostas_diarias(OUT atualizado_em timestamptz,
                                                       OUT duracao_ms integer)
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp
        AS $$
        DECLARE
            v_inicio timestamptz := clock_timestamp();
            v_duracao integer;
        BEGIN
            REFRESH MATERIALIZED VIEW CONCURRENTLY public.mv_respostas_diarias;
            v_duracao := (extract(epoch FROM clock_timestamp() - v_inicio) * 1000)::integer;
            UPDATE public.atualizacoes_mv AS a
               SET atualizado_em = now(), duracao_ms = v_duracao
             WHERE a.nome = 'mv_respostas_diarias'
            RETURNING a.atualizado_em INTO atualizar_mv_respostas_diarias.atualizado_em;
            duracao_ms := v_duracao;
        END
        $$
        """
    )
    op.execute("REVOKE EXECUTE ON FUNCTION atualizar_mv_respostas_diarias() FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION atualizar_mv_respostas_diarias() TO {APP}")


def downgrade() -> None:
    op.execute("DROP FUNCTION atualizar_mv_respostas_diarias()")
    for tabela in PRIVILEGIOS:
        op.execute(f"REVOKE ALL ON {tabela} FROM {APP}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {APP}")
