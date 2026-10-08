"""Experimento: performance das consultas de analytics, com EXPLAIN ANALYZE.

    docker compose exec backend python -m scripts.seed_revisoes --alunos 200
    docker compose exec backend python -m scripts.experimento_analytics

Gera docs/experimentos/analytics.md. Mede, para 20 alunos sorteados:
1. ao vivo (vw_respostas) x materialized view, na evolução diária e no calendário;
2. CTE MATERIALIZED x NOT MATERIALIZED com o filtro FORA da CTE;
3. filtro de período sargable x não sargable;
4. índice com INCLUDE (index-only scan), antes e depois de VACUUM;
5. REFRESH x REFRESH CONCURRENTLY.
Tudo que muda o esquema roda numa transação desfeita no fim (ROLLBACK). O VACUUM
fica de fora: não pode rodar dentro de uma transação.
"""

import re
import statistics
import time
from datetime import timedelta
from pathlib import Path

import psycopg

from app.servicos import analytics as a
from scripts.experimento_fila import medir, para_psycopg
from scripts.seed_experimento import url_psycopg

SAIDA = Path(__file__).resolve().parents[2] / "docs" / "experimentos" / "analytics.md"
AMOSTRA = 20

# Mesma saída de SQL_EVOLUCAO_DIARIA / SQL_CALENDARIO, mas agregando ao vivo a view
# (todo o histórico do aluno, a cada chamada) em vez de ler a MV já agregada.
POR_DIA_AO_VIVO = """
        SELECT r.dia, count(*)::int AS respostas, count(*) FILTER (WHERE r.acertou)::int AS acertos
        FROM vw_respostas AS r
        WHERE r.usuario_id = :usuario_id AND r.dia BETWEEN {inicio} AND :ate {fonte}
        GROUP BY r.dia"""


def evolucao_ao_vivo() -> str:
    sql = a.SQL_EVOLUCAO_DIARIA.format(filtro="")
    inicio = sql.index("    por_dia AS (") + len("    por_dia AS (")
    fim = sql.index("    ),\n    serie AS")
    return sql[:inicio] + POR_DIA_AO_VIVO.format(inicio="CAST(:de AS date) - 6", fonte="") + "\n" + sql[fim:]


def calendario_ao_vivo() -> str:
    sql = a.SQL_CALENDARIO.format(filtro="")
    inicio = sql.index("    por_dia AS (") + len("    por_dia AS (")
    fim = sql.index("    ),\n    serie AS")
    trecho = POR_DIA_AO_VIVO.format(inicio=":de", fonte="AND r.fonte = 'revisao'").replace(
        "count(*)::int AS respostas, count(*) FILTER (WHERE r.acertou)::int AS acertos",
        "count(*)::int AS revisoes")
    return sql[:inicio] + trecho + "\n" + sql[fim:]


CTE_FILTRO_FORA = """
    WITH respostas AS {modo} (
        SELECT * FROM vw_respostas
    )
    SELECT dia, count(*) FROM respostas
    WHERE usuario_id = :usuario_id
    GROUP BY dia
"""

_BASE_PERIODO = """
    SELECT count(*) FROM historico_revisoes AS h
    JOIN flashcards AS f ON f.id = h.flashcard_id
    JOIN disciplinas AS d ON d.id = f.disciplina_id
    JOIN usuarios AS u ON u.id = d.usuario_id
    WHERE d.usuario_id = :usuario_id
"""
# Coluna crua, mas limite dependente de u.fuso_horario (do JOIN): vira Join Filter.
PERIODO_LIMITE_DO_JOIN = _BASE_PERIODO + """
      AND h.revisado_em >= (CAST(:de AS timestamp) AT TIME ZONE u.fuso_horario)
      AND h.revisado_em < (CAST(:ate AS timestamp) + interval '1 day') AT TIME ZONE u.fuso_horario
"""
# Coluna crua e limite como subconsulta escalar (InitPlan): pode virar Index Cond.
PERIODO_SARGABLE = _BASE_PERIODO + a._periodo("h.revisado_em", "x", "x")
# A 1a versão da consulta por material (DISTINCT sobre as associações de todos os alunos).
MATERIAL_ANTES = """
    WITH item_material AS (
        SELECT DISTINCT 'revisao' AS fonte, ft.flashcard_id AS item_id, t.material_id
        FROM flashcard_trechos AS ft JOIN trechos AS t ON t.id = ft.trecho_id
        UNION ALL
        SELECT DISTINCT 'questao', qt.questao_id, t.material_id
        FROM questao_trechos AS qt JOIN trechos AS t ON t.id = qt.trecho_id
    )
""" + a.SQL_ACERTO_SEMANAL_MATERIAL[a.SQL_ACERTO_SEMANAL_MATERIAL.index("    SELECT date_trunc"):].format(filtro="")
PERIODO_NAO_SARGABLE = """
    SELECT count(*) FROM historico_revisoes AS h
    JOIN flashcards AS f ON f.id = h.flashcard_id
    JOIN disciplinas AS d ON d.id = f.disciplina_id
    JOIN usuarios AS u ON u.id = d.usuario_id
    WHERE d.usuario_id = :usuario_id
      AND (h.revisado_em AT TIME ZONE u.fuso_horario)::date BETWEEN :de AND :ate
"""


def linha(nome: str, r: dict) -> str:
    return f"| {nome} | {r['mediana']:.2f} | {r['p95']:.2f} | {r['buffers']} | `{r['caminho']}` |"


def plano_resumido(r: dict) -> str:
    return re.sub(r"'\d{4}-\d\d-\d\d[^']*'", "'...'", r["plano"])


def main() -> None:
    out_linhas: list[str] = []
    out = out_linhas.append
    resultados: dict[str, dict] = {}
    detalhes: list[tuple[str, dict]] = []

    with psycopg.connect(url_psycopg(), autocommit=True) as conn:
        conn.execute("VACUUM (ANALYZE) historico_revisoes")  # mapa de visibilidade em dia
        conn.execute("ANALYZE mv_respostas_diarias")
        totais = conn.execute(
            """SELECT (SELECT count(*) FROM historico_revisoes), (SELECT count(*) FROM tentativas),
                      (SELECT count(*) FROM mv_respostas_diarias),
                      pg_size_pretty(pg_total_relation_size('historico_revisoes')),
                      pg_size_pretty(pg_total_relation_size('mv_respostas_diarias'))"""
        ).fetchone()
        alunos = [u for (u,) in conn.execute(
            "SELECT id FROM usuarios WHERE email LIKE 'aluno-%%@estuda-ai.local' "
            "ORDER BY md5(id::text) LIMIT %s", (AMOSTRA,)
        ).fetchall()]
        if not alunos:
            raise SystemExit("Rode antes: python -m scripts.seed_revisoes --alunos 200")
        hoje = conn.execute(
            "SELECT (now() AT TIME ZONE 'America/Sao_Paulo')::date").fetchone()[0]

    p30 = [{"usuario_id": u, "de": hoje - timedelta(days=29), "ate": hoje} for u in alunos]
    p365 = [{"usuario_id": u, "de": hoje - timedelta(days=364), "ate": hoje} for u in alunos]
    p7 = [{"usuario_id": u, "de": hoje - timedelta(days=6), "ate": hoje} for u in alunos]
    p49 = [{"usuario_id": u, "de": hoje - timedelta(days=48), "ate": hoje} for u in alunos]

    with psycopg.connect(url_psycopg()) as conn, conn.transaction(force_rollback=True):
        cur = psycopg.ClientCursor(conn)
        casos = [
            ("1. Evolução diária (30 dias)", [
                ("ao vivo (vw_respostas)", evolucao_ao_vivo(), p30),
                ("materialized view", a.SQL_EVOLUCAO_DIARIA.format(filtro=""), p30)]),
            ("1. Calendário (365 dias)", [
                ("ao vivo (vw_respostas)", calendario_ao_vivo(), p365),
                ("materialized view", a.SQL_CALENDARIO.format(filtro=""), p365)]),
            ("2. CTE com o filtro do lado de fora", [
                ("AS NOT MATERIALIZED (padrão quando referenciada 1 vez)",
                 CTE_FILTRO_FORA.format(modo="NOT MATERIALIZED"), p30),
                ("AS MATERIALIZED", CTE_FILTRO_FORA.format(modo="MATERIALIZED"), p30)]),
            ("3. Filtro de período (7 dias)", [
                ("não sargable: (coluna AT TIME ZONE ...)::date", PERIODO_NAO_SARGABLE, p7),
                ("coluna crua, limite vindo do JOIN (u.fuso_horario)", PERIODO_LIMITE_DO_JOIN, p7),
                ("sargable: coluna crua + limite em subconsulta (InitPlan)", PERIODO_SARGABLE, p7)]),
            ("6. Acerto semanal por material (7 semanas)", [
                ("antes: DISTINCT sobre as associações de todos os alunos", MATERIAL_ANTES, p49),
                ("depois: só os itens das disciplinas do aluno",
                 a.SQL_ACERTO_SEMANAL_MATERIAL.format(filtro="", filtro_d=""), p49)]),
        ]
        for titulo, variantes in casos:
            print(titulo)
            for nome, sql, params in variantes:
                r = medir(cur, para_psycopg(sql), params)
                resultados[(titulo, nome)] = r
                detalhes.append((f"{titulo} — {nome}", r))
                print(f"  {nome}: {r['mediana']:.2f} ms ({r['caminho'][:90]})")

        # 4. índice com INCLUDE: a sequência (ao vivo) e o ranking leem o histórico
        print("4. Índice com INCLUDE")
        sequencia = a.SQL_SEQUENCIA.format(filtro="")
        dificeis = a.SQL_CARDS_DIFICEIS.format(filtro="", periodo="")
        p_seq = [{"usuario_id": u, "agora": None} for u in alunos]
        p_dif = [{"usuario_id": u, "limite": 5} for u in alunos]
        for rotulo in ("antes: (flashcard_id, revisado_em)", "depois: + INCLUDE (nota)"):
            include = "INCLUDE (nota)" if rotulo.startswith("depois") else ""
            cur.execute("DROP INDEX ix_historico_revisoes_flashcard_revisado_em")
            cur.execute(
                "CREATE INDEX ix_historico_revisoes_flashcard_revisado_em "
                f"ON historico_revisoes (flashcard_id, revisado_em) {include}"
            )
            cur.execute("ANALYZE historico_revisoes")
            for nome, sql, params in (("sequência", sequencia, p_seq), ("cards difíceis", dificeis, p_dif)):
                r = medir(cur, para_psycopg(sql), params)
                resultados[("4", nome, rotulo)] = r
                detalhes.append((f"4. {nome} — {rotulo}", r))
                print(f"  {nome} / {rotulo}: {r['mediana']:.2f} ms ({r['caminho'][:90]})")

        # 5. REFRESH comum x CONCURRENTLY (dentro da transação: desfeito no fim)
        print("5. REFRESH")
        refresh = {}
        for modo in ("", "CONCURRENTLY "):
            tempos = []
            for _ in range(3):
                t = time.perf_counter()
                cur.execute(f"REFRESH MATERIALIZED VIEW {modo}mv_respostas_diarias")
                tempos.append((time.perf_counter() - t) * 1000)
            refresh[modo.strip() or "comum"] = statistics.median(tempos)
        print(f"  {refresh}")

    # ------------------------------------------------------------- relatório
    out("# Experimento: performance do analytics\n")
    out("Gerado por `backend/scripts/experimento_analytics.py` (não edite à mão; rode o script de novo).\n")
    out(f"- {totais[0]:,} revisões e {totais[1]:,} tentativas (seed com 200 alunos); "
        f"`historico_revisoes` com índices: {totais[3]}; `mv_respostas_diarias`: "
        f"{totais[2]:,} linhas, {totais[4]}.")
    out(f"- {AMOSTRA} alunos sorteados por medição, cache aquecido. Tempo = `Execution Time` do "
        "EXPLAIN ANALYZE em ms (mediana e p95). Buffers = páginas de 8 KB tocadas (1o aluno).")
    out("- Mudanças de esquema (índices) e REFRESH rodaram numa transação desfeita no fim.\n")

    for titulo, variantes in casos:
        out(f"## {titulo}\n")
        out("| Variante | Mediana | p95 | Buffers | Plano |")
        out("|---|---:|---:|---:|---|")
        for nome, _, _ in variantes:
            out(linha(nome, resultados[(titulo, nome)]))
        out("")
    out("## 4. Índice com INCLUDE (index-only scan)\n")
    out("| Consulta | Índice | Mediana | p95 | Buffers | Plano |")
    out("|---|---|---:|---:|---:|---|")
    for nome in ("sequência", "cards difíceis"):
        for rotulo in ("antes: (flashcard_id, revisado_em)", "depois: + INCLUDE (nota)"):
            r = resultados[("4", nome, rotulo)]
            out(f"| {nome} | {rotulo} | {r['mediana']:.2f} | {r['p95']:.2f} | {r['buffers']} | "
                f"`{r['caminho']}` |")
    out("")
    out("## 5. REFRESH da materialized view\n")
    out("| Modo | Mediana (3 execuções) |")
    out("|---|---:|")
    for modo, ms in refresh.items():
        out(f"| `REFRESH MATERIALIZED VIEW {'CONCURRENTLY ' if modo != 'comum' else ''}` | {ms:.0f} ms |")
    out("")
    out("## Planos completos (EXPLAIN ANALYZE, 1o aluno)\n")
    for nome, r in detalhes:
        out(f"<details><summary>{nome}</summary>\n")
        out("```\n" + plano_resumido(r) + "\n```\n</details>\n")

    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    SAIDA.write_text("\n".join(out_linhas) + "\n", encoding="utf-8")
    print(f"Resultados em {SAIDA}")


if __name__ == "__main__":
    main()
