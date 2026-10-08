"""Menor privilégio: o que o papel da API (estuda_ai_app) pode e NÃO pode fazer.

Todos os testes daqui (e a suíte inteira) conectam como estuda_ai_app; as
migrations rodaram como dono. Ver a migration papel_app_menor_privilegio.

O teste da matriz lê os privilégios REAIS do catálogo para cada tabela, view e
MV do schema e compara com a tabela abaixo. Uma tabela nova sem GRANT (ou com
privilégio a mais) faz o teste falhar: alguém tem de decidir conscientemente o
que a API pode fazer nela.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.servicos import analytics

PAPEL = "estuda_ai_app"
DML = {"SELECT", "INSERT", "UPDATE", "DELETE"}

ESPERADO = {
    "usuarios": {"SELECT", "INSERT", "UPDATE"},
    "disciplinas": DML,
    "materiais": DML,
    "trechos": DML,
    "flashcards": DML,
    "flashcard_trechos": DML,
    "questoes": DML,
    "questao_trechos": DML,
    "alternativas": DML,
    "revisoes": DML,
    "limites_taxa": DML,
    "historico_revisoes": {"SELECT", "INSERT"},
    "tentativas": {"SELECT", "INSERT"},
    "geracoes": {"SELECT", "INSERT"},
    # Fase 7: a sessão muda de status (UPDATE), mas o app nunca apaga uma; pausas e
    # eventos chegam completos e não mudam mais (como o histórico de revisões)
    "sessoes_estudo": {"SELECT", "INSERT", "UPDATE"},
    "pausas_sessao": {"SELECT", "INSERT"},
    "eventos_foco": {"SELECT", "INSERT"},
    "vw_sessoes_foco": {"SELECT"},
    "vw_respostas": {"SELECT"},
    "mv_respostas_diarias": {"SELECT"},
    "atualizacoes_mv": {"SELECT"},
    "alembic_version": set(),
}

TODOS = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"]


def _negado(session, sql: str, params: dict | None = None):
    with pytest.raises(ProgrammingError) as erro:
        with session.begin_nested():  # o erro desfaz só o savepoint
            session.execute(text(sql), params or {})
    return str(erro.value.orig)


def test_a_suite_roda_como_o_papel_da_api(session):
    assert session.scalar(text("SELECT current_user")) == PAPEL


def test_papel_sem_atributos_de_administrador(session):
    papel = session.execute(
        text("""SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls, rolreplication
                FROM pg_roles WHERE rolname = :p"""),
        {"p": PAPEL},
    ).one()
    assert not any(papel)


def test_matriz_de_privilegios_por_tabela(session):
    """has_table_privilege() responde para tabelas, views E materialized views
    (information_schema.role_table_grants não lista MVs)."""
    linhas = session.execute(
        text("""
            SELECT c.relname, p.privilegio
            FROM pg_class AS c
            CROSS JOIN unnest(CAST(:todos AS text[])) AS p(privilegio)
            WHERE c.relnamespace = 'public'::regnamespace
              AND c.relkind IN ('r', 'p', 'v', 'm')
              AND has_table_privilege(:papel, c.oid, p.privilegio)
        """),
        {"todos": TODOS, "papel": PAPEL},
    ).all()
    tabelas = session.scalars(
        text("""SELECT relname FROM pg_class
                WHERE relnamespace = 'public'::regnamespace AND relkind IN ('r', 'p', 'v', 'm')""")
    ).all()

    real = {t: set() for t in tabelas}
    for tabela, privilegio in linhas:
        real[tabela].add(privilegio)

    assert set(real) == set(ESPERADO), "tabela nova? decida os privilégios dela"
    assert real == ESPERADO


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE disciplinas",
        "TRUNCATE geracoes",
        "ALTER TABLE usuarios ADD COLUMN admin boolean",
        "ALTER TABLE usuarios DROP CONSTRAINT ck_usuarios_senha_hash_argon2id",
        "CREATE TABLE invasora (id int)",
        "CREATE INDEX ix_teste ON disciplinas (nome)",
        "DROP INDEX ix_trechos_embedding_hnsw",
        "CREATE FUNCTION f() RETURNS int LANGUAGE sql AS 'SELECT 1'",
        "ALTER TABLE historico_revisoes DISABLE TRIGGER ALL",
        "DROP MATERIALIZED VIEW mv_respostas_diarias",
        # se dar privilégios: ele não é dono nem tem GRANT OPTION
        "GRANT SELECT ON alembic_version TO estuda_ai_app",
    ],
)
def test_sem_ddl(session, sql):
    """Nada de mudar o schema: só o dono pode (e ele só aparece nas migrations)."""
    erro = _negado(session, sql)
    assert "permission denied" in erro or "must be owner" in erro


@pytest.mark.parametrize(("tabela", "coluna"), [("geracoes", "custo_usd"),
                                                ("historico_revisoes", "nota"),
                                                ("tentativas", "correta")])
def test_historico_e_auditoria_sao_so_insert(session, tabela, coluna):
    """Quem invadir a API não apaga o rastro de gastos nem reescreve o histórico."""
    assert "permission denied" in _negado(session, f"UPDATE {tabela} SET {coluna} = {coluna}")
    assert "permission denied" in _negado(session, f"DELETE FROM {tabela}")


def test_nao_apaga_contas(session, usuario):
    assert "permission denied" in _negado(
        session, "DELETE FROM usuarios WHERE id = :id", {"id": usuario.id}
    )


def test_nao_le_a_versao_do_schema(session):
    assert "permission denied" in _negado(session, "SELECT * FROM alembic_version")


def test_refresh_direto_da_mv_e_negado(session):
    assert "must be owner" in _negado(session, "REFRESH MATERIALIZED VIEW mv_respostas_diarias")


def test_refresh_pela_funcao_security_definer(session):
    """A função roda como dono; o app só tem EXECUTE nela."""
    antes = session.scalar(text("SELECT atualizado_em FROM atualizacoes_mv"))

    atualizacao = analytics.atualizar_mv(session)

    assert atualizacao.atualizado_em is not None and atualizacao.duracao_ms >= 0
    assert antes is None or atualizacao.atualizado_em >= antes


def test_funcao_security_definer_tem_search_path_fixo_e_nao_e_publica(session):
    funcao = session.execute(
        text("""
            SELECT p.prosecdef, p.proconfig,
                   EXISTS (SELECT FROM aclexplode(p.proacl) AS a WHERE a.grantee = 0) AS publica
            FROM pg_proc AS p
            WHERE p.proname = 'atualizar_mv_respostas_diarias'
        """)
    ).one()
    assert funcao.prosecdef
    # pg_temp por último: senão uma tabela temporária com o mesmo nome teria prioridade
    assert funcao.proconfig == ["search_path=pg_catalog, public, pg_temp"]
    assert not funcao.publica  # grantee 0 = PUBLIC (todos os papéis)


def test_app_nao_cria_objetos_no_schema_public(session):
    assert not session.scalar(text("SELECT has_schema_privilege('public', 'CREATE')"))
