"""Experimento: busca vetorial com e sem o índice HNSW, medida com EXPLAIN ANALYZE.

    docker compose exec backend python -m scripts.seed_experimento
    docker compose exec backend python -m scripts.experimento_hnsw

Gera docs/experimentos/hnsw.md com as tabelas e os planos de execução.

Como "tirar o índice" sem estragar o banco: dentro de uma transação,
    BEGIN; DROP INDEX ...; <consultas>; ROLLBACK;
O DDL do Postgres é transacional, então o ROLLBACK traz o índice de volta
intacto. Cuidado: DROP INDEX pega um lock ACCESS EXCLUSIVE na tabela até o fim
da transação, o que bloqueia leituras e escritas de outras conexões. Faça isso
num banco de desenvolvimento, nunca em produção.

Métricas:
- tempo: "Execution Time" do EXPLAIN ANALYZE (mediana e p95 de várias consultas);
- recall@k: dos k vizinhos exatos (busca sem índice), quantos o HNSW devolveu;
- linhas: quantas das k linhas pedidas vieram (o HNSW com filtro pode devolver menos).

Cada cenário roda as consultas uma vez para aquecer o cache antes de medir.
"""

import re
import statistics
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import psycopg

from app.models import EMBEDDING_DIM
from scripts.seed_experimento import EMAIL, url_psycopg

K = 10
CONSULTAS = 50
SAIDA = Path(__file__).resolve().parents[2] / "docs" / "experimentos" / "hnsw.md"

SQL_KNN = """
SELECT id FROM trechos
WHERE disciplina_id = %(d)s
ORDER BY embedding <=> %(q)s::vector
LIMIT %(k)s
"""


def vetor_literal(v: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def gerar_consultas(conn, disciplina_id: int, n: int, rng) -> list[str]:
    """Consultas = trechos existentes da disciplina + ruído (perto dos dados, como
    uma pergunta real, mas sem ser exatamente um ponto já indexado)."""
    linhas = conn.execute(
        # md5(id) em vez de random(): a mesma amostra a cada execução (reprodutível)
        "SELECT embedding::text FROM trechos WHERE disciplina_id = %s"
        " ORDER BY md5(id::text) LIMIT %s",
        (disciplina_id, n),
    ).fetchall()
    consultas = []
    for (texto,) in linhas:
        v = np.array([float(x) for x in texto.strip("[]").split(",")], dtype=np.float32)
        v = v + rng.normal(scale=0.05, size=EMBEDDING_DIM).astype(np.float32)
        consultas.append(vetor_literal(v / np.linalg.norm(v)))
    return consultas


def nos_do_plano(no: dict) -> list[str]:
    nome = no["Node Type"]
    if "Index Name" in no:
        nome += f" ({no['Index Name']})"
    return [nome] + [n for filho in no.get("Plans", []) for n in nos_do_plano(filho)]


@contextmanager
def transacao_descartavel(conn, *comandos: str):
    """Roda comandos (SET LOCAL, DROP INDEX...) numa transação que sempre termina em ROLLBACK."""
    with conn.transaction(force_rollback=True):
        for c in comandos:
            conn.execute(c)
        yield


def medir(conn, disciplina_id: int, consultas: list[str], *comandos: str) -> dict:
    tempos, ids, linhas_devolvidas = [], [], []
    plano_texto = nos = None
    cur = psycopg.ClientCursor(conn)  # interpola os parâmetros no cliente (EXPLAIN não aceita $1)
    with transacao_descartavel(conn, *comandos):
        # Aquecimento: a 1a leitura de cada página do índice vem do disco; as
        # seguintes, do cache (shared_buffers). Sem isto, o primeiro cenário a usar
        # o índice paga a leitura do disco e parece mais lento do que é.
        for q in consultas:
            cur.execute(SQL_KNN, {"d": disciplina_id, "q": q, "k": K})
        for i, q in enumerate(consultas):
            params = {"d": disciplina_id, "q": q, "k": K}
            cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + SQL_KNN, params)
            plano = cur.fetchone()[0][0]
            tempos.append(plano["Execution Time"])
            cur.execute(SQL_KNN, params)
            resultado = [r[0] for r in cur.fetchall()]
            ids.append(resultado)
            linhas_devolvidas.append(len(resultado))
            if i == 0:
                nos = " → ".join(nos_do_plano(plano["Plan"]))
                cur.execute("EXPLAIN (ANALYZE, BUFFERS, COSTS OFF) " + SQL_KNN, params)
                plano_texto = re.sub(
                    r"'\[[^\]]{50,}\]'", "'[...384 números...]'",
                    "\n".join(r[0] for r in cur.fetchall()),
                )
    tempos.sort()
    return {
        "mediana": statistics.median(tempos),
        "p95": tempos[int(0.95 * (len(tempos) - 1))],
        "ids": ids,
        "linhas": statistics.mean(linhas_devolvidas),
        "nos": nos,
        "plano": plano_texto,
    }


def recall(aproximado: list[list[int]], exato: list[list[int]]) -> float:
    return statistics.mean(
        len(set(a) & set(e)) / len(e) for a, e in zip(aproximado, exato, strict=True) if e
    )


def medir_carga(conn) -> tuple[float, float]:
    """INSERT linha a linha com o HNSW presente x a taxa do COPY sem índice."""
    material_id, disciplina_id = conn.execute(
        "SELECT m.id, m.disciplina_id FROM materiais m JOIN disciplinas d ON d.id = m.disciplina_id"
        " JOIN usuarios u ON u.id = d.usuario_id WHERE u.email = %s LIMIT 1",
        (EMAIL,),
    ).fetchone()
    rng = np.random.default_rng(7)
    n = 2000
    linhas = [
        (material_id, disciplina_id, 100_000 + i, "texto", vetor_literal(v / np.linalg.norm(v)))
        for i, v in enumerate(rng.normal(size=(n, EMBEDDING_DIM)).astype(np.float32))
    ]
    with conn.transaction(force_rollback=True):
        t = time.perf_counter()
        conn.cursor().executemany(
            "INSERT INTO trechos (material_id, disciplina_id, ordem, conteudo, embedding)"
            " VALUES (%s, %s, %s, %s, %s::vector)",
            linhas,
        )
        com_indice = n / (time.perf_counter() - t)
    with conn.transaction(force_rollback=True):
        conn.execute("DROP INDEX ix_trechos_embedding_hnsw")
        t = time.perf_counter()
        conn.cursor().executemany(
            "INSERT INTO trechos (material_id, disciplina_id, ordem, conteudo, embedding)"
            " VALUES (%s, %s, %s, %s, %s::vector)",
            linhas,
        )
        sem_indice = n / (time.perf_counter() - t)
    return com_indice, sem_indice


def main() -> None:
    rng = np.random.default_rng(123)
    linhas_md: list[str] = []
    out: Callable[[str], None] = linhas_md.append

    with psycopg.connect(url_psycopg(), autocommit=True) as conn:
        disciplinas = conn.execute(
            """SELECT d.id, d.nome, count(t.id) AS n
               FROM disciplinas d JOIN usuarios u ON u.id = d.usuario_id
               LEFT JOIN trechos t ON t.disciplina_id = d.id
               WHERE u.email = %s GROUP BY d.id, d.nome ORDER BY n DESC""",
            (EMAIL,),
        ).fetchall()
        if not disciplinas:
            raise SystemExit("Rode antes: python -m scripts.seed_experimento")
        total = conn.execute("SELECT count(*) FROM trechos").fetchone()[0]
        grande, pequena = disciplinas[0], disciplinas[-1]
        versao = conn.execute(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()[0]
        tamanhos = conn.execute(
            """SELECT pg_size_pretty(pg_table_size('trechos')),
                      pg_size_pretty(pg_relation_size('ix_trechos_embedding_hnsw'))"""
        ).fetchone()

        out("# Experimento: busca vetorial com e sem HNSW\n")
        out("Gerado por `backend/scripts/experimento_hnsw.py` "
            "(não edite à mão; rode o script de novo).\n")
        out(f"- {total:,} trechos com `vector({EMBEDDING_DIM})`, pgvector {versao}, "
            f"Postgres no Docker (Colima, 2 CPUs, 4 GB).")
        out(f"- Tabela `trechos`: {tamanhos[0]}; índice HNSW (m=16, ef_construction=64): "
            f"{tamanhos[1]}.")
        out(f"- {CONSULTAS} consultas por cenário, k = {K}. Tempo = `Execution Time` do "
            "EXPLAIN ANALYZE (não inclui rede nem o cálculo do embedding).")
        out("- Recall@10 = fração dos 10 vizinhos exatos que a busca devolveu.")
        out("- Cache aquecido: cada cenário roda as consultas uma vez antes de medir.\n")

        for titulo, (disc_id, nome, n) in [
            ("A — disciplina grande", grande),
            ("B — disciplina pequena", pequena),
        ]:
            print(f"Cenário {titulo}: {nome} ({n} trechos)")
            consultas = gerar_consultas(conn, disc_id, CONSULTAS, rng)
            exata = medir(
                conn, disc_id, consultas,
                "DROP INDEX ix_trechos_embedding_hnsw",
            )
            cenarios = [("Exata, sem HNSW (DROP INDEX na transação)", exata)]
            if titulo.startswith("A"):
                for ef in (40, 100, 200):
                    cenarios.append((
                        f"HNSW, ef_search = {ef}",
                        medir(conn, disc_id, consultas,
                              f"SET LOCAL hnsw.ef_search = {ef}",
                              "SET LOCAL hnsw.iterative_scan = strict_order"),
                    ))
            else:
                cenarios.append((
                    "Planejador decide (todos os índices presentes)",
                    medir(conn, disc_id, consultas,
                          "SET LOCAL hnsw.iterative_scan = strict_order"),
                ))
                forcar = ("DROP INDEX ix_trechos_disciplina_id", "SET LOCAL enable_seqscan = off")
                cenarios.append((
                    "HNSW forçado, iterative_scan = off",
                    medir(conn, disc_id, consultas, *forcar, "SET LOCAL hnsw.iterative_scan = off"),
                ))
                cenarios.append((
                    "HNSW forçado, iterative_scan = strict_order",
                    medir(conn, disc_id, consultas, *forcar,
                          "SET LOCAL hnsw.iterative_scan = strict_order"),
                ))

            out(f"## Cenário {titulo}: \"{nome}\" ({n:,} trechos, "
                f"{100 * n / total:.1f}% da tabela)\n")
            out("| Estratégia | Mediana (ms) | p95 (ms) | Recall@10 | Linhas devolvidas | Plano |")
            out("|---|---:|---:|---:|---:|---|")
            for nome_cenario, r in cenarios:
                out(f"| {nome_cenario} | {r['mediana']:.2f} | {r['p95']:.2f} | "
                    f"{recall(r['ids'], exata['ids']):.3f} | {r['linhas']:.1f} de {K} | "
                    f"`{r['nos']}` |")
                print(f"  {nome_cenario}: mediana {r['mediana']:.2f} ms, "
                      f"recall {recall(r['ids'], exata['ids']):.3f}, linhas {r['linhas']:.1f}")
            out("")
            for nome_cenario, r in cenarios:
                out(f"<details><summary>EXPLAIN ANALYZE — {nome_cenario}</summary>\n")
                out("```\n" + r["plano"] + "\n```\n</details>\n")

        print("Medindo carga...")
        com_indice, sem_indice = medir_carga(conn)
        out("## Carga: o custo de manter o índice\n")
        out("INSERT de 2.000 trechos linha a linha (executemany), desfeito com ROLLBACK:\n")
        out("| Situação | Linhas por segundo |")
        out("|---|---:|")
        out(f"| Com o índice HNSW | {com_indice:,.0f} |")
        out(f"| Sem o índice HNSW | {sem_indice:,.0f} |")
        out("")
        print(f"  INSERT com HNSW: {com_indice:,.0f}/s; sem: {sem_indice:,.0f}/s")

    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    SAIDA.write_text("\n".join(linhas_md) + "\n", encoding="utf-8")
    print(f"Resultados em {SAIDA}")


if __name__ == "__main__":
    main()
