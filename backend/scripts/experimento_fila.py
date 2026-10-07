"""Experimento: qual índice serve a fila do dia? Medido com EXPLAIN ANALYZE.

    docker compose exec backend python -m scripts.experimento_fila

Gera docs/experimentos/fila-do-dia.md.

Tudo acontece numa transação que termina em ROLLBACK: os 200 mil cards de
teste, os índices de cada variante e as estatísticas somem no fim, e o banco
fica como estava. (DDL também é transacional; o preço é que DROP/CREATE INDEX
seguram um lock forte em revisoes até o fim; só em banco de desenvolvimento.)

Cenário: 50 usuários x 4 disciplinas x 1.000 cards = 200 mil cards, com
proxima_revisao espalhada entre 30 dias atrás e 60 dias à frente (cerca de 1/3
vencidos). A consulta medida é a MESMA que a API usa (app/servicos/revisao.py):
a fila de um usuário (todas as disciplinas) e a de uma disciplina, LIMIT 20.
"""

import re
import statistics
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from app.servicos.revisao import _COLUNAS, SQL_FILA_DISCIPLINA, SQL_FILA_TODAS
from scripts.seed_experimento import url_psycopg

DISCIPLINAS_POR_USUARIO = 4
# Mesmo total (200 mil cards), divididos entre poucos ou muitos usuários.
CENARIOS = [("A: 50 usuários x 1.000 cards por disciplina", 50, 1000),
            ("B: 500 usuários x 100 cards por disciplina", 500, 100)]

# A 1a versão da fila do usuário (JOIN simples), mantida para comparar com o LATERAL.
SQL_FILA_TODAS_JOIN = f"""
    SELECT {_COLUNAS}
    FROM revisoes AS r
    JOIN disciplinas AS d ON d.id = r.disciplina_id
    JOIN flashcards AS f ON f.id = r.flashcard_id
    WHERE d.usuario_id = :usuario_id AND r.proxima_revisao < :fim_de_hoje
    ORDER BY r.proxima_revisao, r.flashcard_id
    LIMIT :limite
"""
CONSULTAS_MEDIDAS = [
    ("Usuário (JOIN)", SQL_FILA_TODAS_JOIN, "todas"),
    ("Usuário (LATERAL, a da API)", SQL_FILA_TODAS, "todas"),
    ("1 disciplina", SQL_FILA_DISCIPLINA, "disc"),
]


def para_psycopg(sql: str) -> str:
    """:nome (estilo SQLAlchemy) -> %(nome)s (estilo psycopg), sem tocar em casts ::tipo."""
    return re.sub(r"(?<!:):(\w+)", r"%(\1)s", sql)

CONSULTAS = 30
SAIDA = Path(__file__).resolve().parents[2] / "docs" / "experimentos" / "fila-do-dia.md"

INDICE_ATUAL = "ix_revisoes_disciplina_proxima_revisao"
VARIANTES = [
    ("Sem índice de fila (só a PK)", []),
    ("Simples: (proxima_revisao)", ["CREATE INDEX exp_proxima ON revisoes (proxima_revisao)"]),
    ("Simples: (disciplina_id)", ["CREATE INDEX exp_disciplina ON revisoes (disciplina_id)"]),
    (
        "Composto: (disciplina_id, proxima_revisao) — o escolhido",
        [f"CREATE INDEX {INDICE_ATUAL} ON revisoes (disciplina_id, proxima_revisao)"],
    ),
    (
        "Composto invertido: (proxima_revisao, disciplina_id)",
        ["CREATE INDEX exp_invertido ON revisoes (proxima_revisao, disciplina_id)"],
    ),
]


@contextmanager
def transacao_descartavel(conn):
    with conn.transaction(force_rollback=True):
        yield


def nos(plano: dict) -> list[str]:
    nome = plano["Node Type"] + (f" ({plano['Index Name']})" if "Index Name" in plano else "")
    return [nome] + [n for filho in plano.get("Plans", []) for n in nos(filho)]


def medir(cur, sql: str, params_lista: list[dict]) -> dict:
    for p in params_lista:  # aquecimento (cache)
        cur.execute(sql, p)
    tempos, primeiro = [], None
    for p in params_lista:
        cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, p)
        plano = cur.fetchone()[0][0]
        tempos.append(plano["Execution Time"])
        if primeiro is None:
            primeiro = p
            caminho = " → ".join(nos(plano["Plan"]))
            buffers = plano["Plan"].get("Shared Hit Blocks", 0) + plano["Plan"].get("Shared Read Blocks", 0)
    cur.execute("EXPLAIN (ANALYZE, BUFFERS, COSTS OFF) " + sql, primeiro)
    texto = "\n".join(r[0] for r in cur.fetchall())
    tempos.sort()
    return {
        "mediana": statistics.median(tempos),
        "p95": tempos[int(0.95 * (len(tempos) - 1))],
        "caminho": caminho,
        "buffers": buffers,
        "plano": texto,
    }


def gerar_cenario(cur, usuarios: int, cards_por_disciplina: int) -> dict:
    cur.execute(
        """INSERT INTO usuarios (nome, email)
           SELECT 'exp ' || u, 'exp-fila-' || u || '@x.local' FROM generate_series(1, %s) AS u""",
        (usuarios,),
    )
    cur.execute(
        """INSERT INTO disciplinas (usuario_id, nome)
           SELECT u.id, 'Disciplina ' || n FROM usuarios u, generate_series(1, %s) AS n
           WHERE u.email LIKE 'exp-fila-%%'""",
        (DISCIPLINAS_POR_USUARIO,),
    )
    # O trigger cria uma linha em revisoes para cada card.
    cur.execute(
        """INSERT INTO flashcards (disciplina_id, frente, verso)
           SELECT d.id, 'Pergunta ' || n, 'Resposta ' || n
           FROM disciplinas d JOIN usuarios u ON u.id = d.usuario_id,
                generate_series(1, %s) AS n
           WHERE u.email LIKE 'exp-fila-%%'""",
        (cards_por_disciplina,),
    )
    cur.execute(
        """UPDATE revisoes r SET proxima_revisao = now() + (random() * 90 - 30) * interval '1 day'
           FROM disciplinas d JOIN usuarios u ON u.id = d.usuario_id
           WHERE d.id = r.disciplina_id AND u.email LIKE 'exp-fila-%%'"""
    )
    total, vencidos = cur.execute(
        "SELECT count(*), count(*) FILTER (WHERE proxima_revisao < now()) FROM revisoes"
    ).fetchone()
    alvos = cur.execute(
        """SELECT u.id, min(d.id) FROM usuarios u JOIN disciplinas d ON d.usuario_id = u.id
           WHERE u.email LIKE 'exp-fila-%%' GROUP BY u.id ORDER BY random() LIMIT %s""",
        (CONSULTAS,),
    ).fetchall()
    return {"total": total, "vencidos": vencidos, "alvos": alvos}


def aplicar_variante(cur, ddl: list[str]) -> None:
    cur.execute(f"DROP INDEX IF EXISTS {INDICE_ATUAL}")
    for extra in ("exp_proxima", "exp_disciplina", "exp_invertido"):
        cur.execute(f"DROP INDEX IF EXISTS {extra}")
    for comando in ddl:
        cur.execute(comando)
    cur.execute("ANALYZE revisoes")


def main() -> None:
    linhas: list[str] = []
    out = linhas.append
    agora = datetime.now(UTC)
    medicoes = []  # (cenario, dados, [(variante, {consulta: resultado})])

    with psycopg.connect(url_psycopg()) as conn, transacao_descartavel(conn):
        cur = psycopg.ClientCursor(conn)  # interpola parâmetros (EXPLAIN não aceita $1)
        fim_de_hoje = cur.execute(
            "SELECT (date_trunc('day', now() AT TIME ZONE 'America/Sao_Paulo') + interval '1 day')"
            " AT TIME ZONE 'America/Sao_Paulo'"
        ).fetchone()[0]

        # O índice parcial que "seria ideal" não pode existir:
        cur.execute("SAVEPOINT parcial")
        try:
            cur.execute(
                "CREATE INDEX exp_parcial ON revisoes (disciplina_id, proxima_revisao) "
                "WHERE proxima_revisao <= now()"
            )
            erro_parcial = "(criado?!)"
        except psycopg.Error as e:
            erro_parcial = str(e).strip().splitlines()[0]
        cur.execute("ROLLBACK TO SAVEPOINT parcial")

        for nome_cenario, usuarios, cards in CENARIOS:
            print(f"cenário {nome_cenario}: gerando...")
            cur.execute("SAVEPOINT cenario")
            dados = gerar_cenario(cur, usuarios, cards)
            base = {"agora": agora, "fim_de_hoje": fim_de_hoje, "limite": 20}
            params = {
                "todas": [base | {"usuario_id": u} for u, _ in dados["alvos"]],
                "disc": [base | {"usuario_id": u, "disciplina_id": d} for u, d in dados["alvos"]],
            }
            por_variante = []
            for nome_variante, ddl in VARIANTES:
                aplicar_variante(cur, ddl)
                por_variante.append((nome_variante, {
                    rotulo: medir(cur, para_psycopg(sql), params[tipo])
                    for rotulo, sql, tipo in CONSULTAS_MEDIDAS
                }))
                print(f"  {nome_variante}: " + " | ".join(
                    f"{r}: {m['mediana']:.2f} ms" for r, m in por_variante[-1][1].items()))
            medicoes.append((nome_cenario, usuarios, cards, dados, por_variante))
            cur.execute("ROLLBACK TO SAVEPOINT cenario")

    out("# Experimento: índice da fila do dia\n")
    out("Gerado por `backend/scripts/experimento_fila.py` (não edite à mão; rode o script de novo).\n")
    out("- Dois cenários com o MESMO total de 200 mil cards em `revisoes`, divididos entre poucos "
        "ou muitos usuários (4 disciplinas cada). `proxima_revisao` espalhada entre 30 dias atrás "
        "e 60 à frente (~1/3 vencidos). Tudo criado e desfeito numa transação (ROLLBACK).")
    out(f"- {CONSULTAS} usuários sorteados por medição, `LIMIT 20`, cache aquecido. Tempo = "
        "`Execution Time` do EXPLAIN ANALYZE, em milissegundos (mediana; p95 entre parênteses).")
    out("- Consultas: \"Usuário (JOIN)\" foi a 1a versão; \"Usuário (LATERAL)\" e \"1 disciplina\" "
        "são as da API (`app/servicos/revisao.py`).\n")
    for nome_cenario, usuarios, cards, dados, por_variante in medicoes:
        out(f"## Cenário {nome_cenario}\n")
        out(f"{dados['total']:,} linhas em `revisoes`, {dados['vencidos']:,} vencidas; "
            f"cada usuário tem {DISCIPLINAS_POR_USUARIO * cards:,} cards "
            f"({100 / usuarios:.1f}% do total).\n")
        cabecalho = " | ".join(r for r, _, _ in CONSULTAS_MEDIDAS)
        out(f"| Índice | {cabecalho} |")
        out("|---|" + "---:|" * len(CONSULTAS_MEDIDAS))
        for nome_variante, res in por_variante:
            celulas = " | ".join(
                f"{res[r]['mediana']:.2f} ({res[r]['p95']:.2f})" for r, _, _ in CONSULTAS_MEDIDAS
            )
            out(f"| {nome_variante} | {celulas} |")
        out("")
        out("<details><summary>Caminho escolhido pelo planejador em cada caso</summary>\n")
        out("| Índice | Consulta | Plano | Buffers |")
        out("|---|---|---|---:|")
        for nome_variante, res in por_variante:
            for r, _, _ in CONSULTAS_MEDIDAS:
                out(f"| {nome_variante} | {r} | `{res[r]['caminho']}` | {res[r]['buffers']} |")
        out("\n</details>\n")

    out("## E o índice parcial?\n")
    out("O índice \"perfeito\" para a fila conteria só as linhas vencidas:\n")
    out("```sql\nCREATE INDEX ... ON revisoes (disciplina_id, proxima_revisao)\n"
        "WHERE proxima_revisao <= now();\n```\n")
    out(f"O Postgres recusa: `{erro_parcial}`\n")
    out("O predicado de um índice parcial é avaliado quando a linha é gravada, e o resultado "
        "precisa ser sempre o mesmo. `now()` muda a cada instante: um card que não estava "
        "vencido ao ser gravado venceria depois sem nunca entrar no índice. Ver "
        "`docs/repeticao-espacada.md`.\n")

    _, _, _, _, por_variante = medicoes[0]
    escolhido = dict(por_variante)[VARIANTES[3][0]]
    for r, _, _ in CONSULTAS_MEDIDAS:
        plano = re.sub(r"'\d{4}-\d\d-\d\d[^']*'", "'...'", escolhido[r]["plano"])
        out(f"<details><summary>EXPLAIN ANALYZE — índice escolhido, cenário A — {r}</summary>\n")
        out("```\n" + plano + "\n```\n</details>\n")

    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    SAIDA.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    print(f"índice parcial com now(): {erro_parcial}")
    print(f"Resultados em {SAIDA}")


if __name__ == "__main__":
    main()
