"""O que o modo foco bloqueia (sites e programas) e as preferências do bloqueio.

Quem aplica o bloqueio é o app desktop (arquivo hosts e monitor de processos); aqui
fica só a configuração, para valer nos dois computadores.
"""

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.schemas import Bloqueios

# Trava a linha de preferências do usuário (UPSERT trava a linha que insere ou
# atualiza até o COMMIT). Dois salvamentos simultâneos (os dois computadores) passam a
# acontecer um depois do outro: sem isso, o DELETE de um e o INSERT do outro se
# misturariam, e a lista final seria a UNIÃO das duas, que ninguém escolheu.
SQL_PREFERENCIAS = text("""
    INSERT INTO preferencias_foco (usuario_id, bloquear_sites, bloquear_programas, espera_emergencia_s)
    VALUES (:u, :sites, :programas, :espera)
    ON CONFLICT (usuario_id) DO UPDATE SET
        bloquear_sites = EXCLUDED.bloquear_sites,
        bloquear_programas = EXCLUDED.bloquear_programas,
        espera_emergencia_s = EXCLUDED.espera_emergencia_s
""")

# Troca a lista inteira num comando só, com uma CTE que MODIFICA dados:
#  - `apagados` remove o que saiu da lista;
#  - o INSERT põe o que entrou; o que já existia bate no UNIQUE e fica como está
#    (ON CONFLICT DO NOTHING), com o criado_em original.
# As duas partes enxergam o MESMO snapshot (o do início do comando): o INSERT não "vê"
# o DELETE, e vice-versa. Aqui isso não atrapalha, porque os dois mexem em linhas
# diferentes (as que saíram x as que entraram).
# Por que não apagar tudo e inserir de novo: funciona, mas reescreve todas as linhas a
# cada salvamento (mais WAL, mais tuplas mortas para o VACUUM) e perde o criado_em.
# No Postgres 17 daria para usar MERGE ... WHEN NOT MATCHED BY SOURCE THEN DELETE; o
# servidor é 16, que tem MERGE mas não essa cláusula.
SQL_SITES = text("""
    WITH lista AS (
        SELECT DISTINCT d AS dominio FROM unnest(CAST(:dominios AS text[])) AS d
    ),
    apagados AS (
        DELETE FROM sites_bloqueados s
        WHERE s.usuario_id = :u
          AND NOT EXISTS (SELECT 1 FROM lista l WHERE l.dominio = s.dominio)
    )
    INSERT INTO sites_bloqueados (usuario_id, dominio)
    SELECT :u, dominio FROM lista
    ON CONFLICT (usuario_id, dominio) DO NOTHING
""")

# Igual, mas a identidade de um programa é (sistema, lower(nome)): "Discord" e "discord"
# são o mesmo. O ON CONFLICT aponta para o índice único de EXPRESSÃO (o Postgres o
# encontra pela lista de colunas/expressões). DISTINCT ON tira repetições da própria
# lista antes do INSERT: sem isso, "Discord" e "discord" no mesmo envio fariam o INSERT
# bater duas vezes na mesma linha ("ON CONFLICT DO UPDATE command cannot affect row a
# second time" no DO UPDATE; no DO NOTHING passaria, mas qual fica seria acaso).
# WITH ORDINALITY numera os itens na ordem em que chegaram: entre repetidos, fica o
# primeiro que você digitou (ordenar por `nome` dependeria da collation do banco).
SQL_PROGRAMAS = text("""
    WITH lista AS (
        SELECT DISTINCT ON (sistema, lower(nome)) sistema, nome
        FROM unnest(CAST(:sistemas AS text[]), CAST(:nomes AS text[]))
             WITH ORDINALITY AS t(sistema, nome, posicao)
        ORDER BY sistema, lower(nome), posicao
    ),
    apagados AS (
        DELETE FROM programas_bloqueados p
        WHERE p.usuario_id = :u
          AND NOT EXISTS (
              SELECT 1 FROM lista l WHERE l.sistema = p.sistema AND lower(l.nome) = lower(p.nome)
          )
    )
    INSERT INTO programas_bloqueados (usuario_id, sistema, nome)
    SELECT :u, sistema, nome FROM lista
    ON CONFLICT (usuario_id, sistema, lower(nome)) DO NOTHING
""")


def salvar(session: Session, *, usuario_id: int, dados: Bloqueios) -> None:
    """Preferências + as duas listas numa transação: ou tudo, ou nada."""
    session.execute(SQL_PREFERENCIAS, {
        "u": usuario_id, "sites": dados.bloquear_sites, "programas": dados.bloquear_programas,
        "espera": dados.espera_emergencia_s,
    })
    session.execute(SQL_SITES, {"u": usuario_id, "dominios": dados.sites})
    programas = [("macos", n) for n in dados.programas.macos] + [
        ("windows", n) for n in dados.programas.windows
    ]
    session.execute(SQL_PROGRAMAS, {
        "u": usuario_id, "sistemas": [s for s, _ in programas], "nomes": [n for _, n in programas],
    })
    session.commit()


def ler(session: Session, *, usuario_id: int) -> Bloqueios:
    # Sem linha de preferências (nunca salvou): os padrões da tabela, lidos do schema
    pref = session.execute(
        text("""SELECT bloquear_sites, bloquear_programas, espera_emergencia_s
                FROM preferencias_foco WHERE usuario_id = :u"""),
        {"u": usuario_id},
    ).mappings().one_or_none()
    sites = session.scalars(
        text("SELECT dominio FROM sites_bloqueados WHERE usuario_id = :u ORDER BY dominio"),
        {"u": usuario_id},
    ).all()
    programas: dict[str, list[str]] = {"macos": [], "windows": []}
    for sistema, nome in session.execute(
        text("""SELECT sistema, nome FROM programas_bloqueados
                WHERE usuario_id = :u ORDER BY sistema, lower(nome)"""),
        {"u": usuario_id},
    ):
        programas[sistema].append(nome)
    return Bloqueios.model_validate({**(pref or {}), "sites": sites, "programas": programas})
