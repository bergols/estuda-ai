"""Analytics das sessões de estudo (fase 7), em SQL explícito e comentado.

As mesmas convenções de analytics.py (fase 5): dia e semana no fuso do usuário,
séries densificadas com generate_series, taxas como razão das somas, filtro de
período "sargable" sobre a coluna crua (_periodo). Explicação e EXPLAIN em
docs/analytics.md, seção 11.

Fonte: a view vw_sessoes_foco (foco efetivo, pausas, tempo fora, interrupções,
dia local). Sessões em andamento ficam de fora de tudo (ainda não têm duração real).
"""

from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.servicos.analytics import _filtro, _periodo

METODOS = ("pomodoro", "bloco", "52_17", "personalizado")


# ============================================================================
# 1. Horas de foco por dia ou semana: densificar + LEFT JOIN
# ============================================================================
#
# - O filtro de período usa v.iniciada_em CRUA (_periodo): a view é "inlined" pelo
#   planejador, a condição desce até sessoes_estudo e o índice (usuario_id,
#   iniciada_em) atende. Filtrar por v.dia (uma expressão AT TIME ZONE) faria o
#   Postgres calcular o dia de TODAS as sessões do usuário antes de filtrar.
# - date_trunc('week', ...) leva o dia para a segunda-feira (semana ISO).
# - A grade (generate_series) garante uma linha por dia/semana, com 0 quando não houve
#   sessão: no gráfico, um dia sem estudo aparece como zero, não some.
SQL_HORAS = """
    WITH por_periodo AS (
        SELECT date_trunc(:unidade, v.dia)::date AS periodo,
               sum(v.foco_efetivo_s)::int AS foco_s,
               count(*)::int AS sessoes,
               count(*) FILTER (WHERE v.status = 'concluida')::int AS concluidas
        FROM vw_sessoes_foco AS v
        WHERE v.usuario_id = :usuario_id
          AND v.status <> 'em_andamento'
          {periodo}
          {filtro}
        GROUP BY 1
    ),
    grade AS (
        SELECT generate_series(date_trunc(:unidade, CAST(:de AS date)),
                               CAST(:ate AS date), CAST('1 ' || :unidade AS interval))::date AS periodo
    )
    SELECT g.periodo,
           coalesce(p.foco_s, 0) AS foco_s,
           coalesce(p.sessoes, 0) AS sessoes,
           coalesce(p.concluidas, 0) AS concluidas
    FROM grade AS g
    LEFT JOIN por_periodo AS p USING (periodo)
    ORDER BY g.periodo
"""


def horas(session: Session, *, usuario_id: int, disciplina_id: int | None, de: date, ate: date,
          unidade: str) -> list[dict]:
    assert unidade in ("day", "week")
    sql = SQL_HORAS.format(
        periodo=_periodo("v.iniciada_em", de, ate), filtro=_filtro("v.disciplina_id", disciplina_id)
    )
    parametros = {"usuario_id": usuario_id, "disciplina_id": disciplina_id, "de": de, "ate": ate,
                  "unidade": unidade}
    return [dict(r) for r in session.execute(text(sql), parametros).mappings()]


# ============================================================================
# 2. Concluídas × abandonadas por método: ROLLUP + GROUPING + grade de métodos
# ============================================================================
#
# - ROLLUP (metodo) = GROUPING SETS ((metodo), ()): as linhas por método E uma linha
#   de total, numa passada só. GROUPING(metodo) = 1 marca a linha de total.
# - A grade (VALUES com os 4 métodos + 'todos') faz um método nunca usado aparecer
#   com zero, sempre na mesma ordem: a cor do gráfico segue o método, não a posição.
SQL_SESSOES = """
    WITH agregado AS (
        SELECT CASE WHEN GROUPING(v.metodo) = 1 THEN 'todos' ELSE v.metodo END AS metodo,
               count(*)::int AS sessoes,
               count(*) FILTER (WHERE v.status = 'concluida')::int AS concluidas,
               count(*) FILTER (WHERE v.status = 'abandonada')::int AS abandonadas,
               avg(v.foco_efetivo_s)::int AS foco_medio_s
        FROM vw_sessoes_foco AS v
        WHERE v.usuario_id = :usuario_id
          AND v.status <> 'em_andamento'
          {periodo}
          {filtro}
        GROUP BY ROLLUP (v.metodo)
    )
    SELECT g.metodo,
           coalesce(a.sessoes, 0) AS sessoes,
           coalesce(a.concluidas, 0) AS concluidas,
           coalesce(a.abandonadas, 0) AS abandonadas,
           round(a.concluidas::numeric / nullif(a.sessoes, 0), 4) AS taxa_conclusao,
           a.foco_medio_s
    FROM (VALUES ('pomodoro', 1), ('bloco', 2), ('52_17', 3), ('personalizado', 4), ('todos', 5))
         AS g(metodo, ordem)
    LEFT JOIN agregado AS a USING (metodo)
    ORDER BY g.ordem
"""


def sessoes_por_metodo(session: Session, *, usuario_id: int, disciplina_id: int | None, de: date,
                       ate: date) -> list[dict]:
    sql = SQL_SESSOES.format(
        periodo=_periodo("v.iniciada_em", de, ate), filtro=_filtro("v.disciplina_id", disciplina_id)
    )
    parametros = {"usuario_id": usuario_id, "disciplina_id": disciplina_id, "de": de, "ate": ate}
    return [dict(r) for r in session.execute(text(sql), parametros).mappings()]


# ============================================================================
# 3. Interrupções por sessão: as sessões mais recentes do período, em ordem
# ============================================================================
#
# Interrupções por HORA de foco além do total: uma sessão de 2 h com 4 interrupções
# foi mais concentrada que uma de 25 min com 3. nullif evita dividir por zero (sessão
# encerrada antes de qualquer foco).
SQL_INTERRUPCOES = """
    SELECT *
    FROM (
        SELECT v.id AS sessao_id, v.iniciada_em, v.dia, v.metodo, v.status,
               v.interrupcoes, v.fora_s, v.foco_efetivo_s,
               round(v.interrupcoes * 3600.0 / nullif(v.foco_efetivo_s, 0), 2) AS por_hora
        FROM vw_sessoes_foco AS v
        WHERE v.usuario_id = :usuario_id
          AND v.status <> 'em_andamento'
          {periodo}
          {filtro}
        ORDER BY v.iniciada_em DESC
        LIMIT :limite
    ) AS recentes
    ORDER BY iniciada_em
"""


def interrupcoes(session: Session, *, usuario_id: int, disciplina_id: int | None, de: date,
                 ate: date, limite: int) -> list[dict]:
    sql = SQL_INTERRUPCOES.format(
        periodo=_periodo("v.iniciada_em", de, ate), filtro=_filtro("v.disciplina_id", disciplina_id)
    )
    parametros = {"usuario_id": usuario_id, "disciplina_id": disciplina_id, "de": de, "ate": ate,
                  "limite": limite}
    return [dict(r) for r in session.execute(text(sql), parametros).mappings()]


# ============================================================================
# 4. Acerto logo depois de cada método: LEFT JOIN LATERAL (top-1 por linha)
# ============================================================================
#
# Para CADA resposta (revisão de card ou questão, de vw_respostas), a subconsulta
# LATERAL acha a última sessão do usuário que terminou até :janela minutos antes. É o
# "top-1 por grupo" da fila do dia (fase 4): ORDER BY terminada_em DESC LIMIT 1, que o
# índice (usuario_id, terminada_em) INCLUDE (metodo) responde lendo UMA entrada
# (Index Only Scan Backward). LEFT JOIN: resposta sem sessão antes continua na conta,
# no grupo 'sem_sessao', que é a base de comparação.
#
# Cuidado na leitura: é correlação, não causa. Se você só faz pomodoro quando está
# descansado, o pomodoro "ganha" por isso. E poucas respostas num grupo tornam a taxa
# instável: a resposta traz o n de cada grupo para a tela avisar.
SQL_ACERTO_POS_SESSAO = """
    WITH classificadas AS (
        SELECT coalesce(ultima.metodo, 'sem_sessao') AS grupo, r.acertou
        FROM vw_respostas AS r
        LEFT JOIN LATERAL (
            SELECT s.metodo
            FROM sessoes_estudo AS s
            WHERE s.usuario_id = r.usuario_id
              AND s.terminada_em IS NOT NULL
              AND s.terminada_em <= r.respondido_em
              AND s.terminada_em > r.respondido_em - make_interval(mins => :janela_min)
            ORDER BY s.terminada_em DESC
            LIMIT 1
        ) AS ultima ON true
        WHERE r.usuario_id = :usuario_id
          {periodo}
          {filtro}
    ),
    agregado AS (
        SELECT grupo, count(*)::int AS respostas, count(*) FILTER (WHERE acertou)::int AS acertos
        FROM classificadas
        GROUP BY grupo
    )
    SELECT g.grupo,
           coalesce(a.respostas, 0) AS respostas,
           coalesce(a.acertos, 0) AS acertos,
           round(a.acertos::numeric / nullif(a.respostas, 0), 4) AS taxa
    FROM (VALUES ('pomodoro', 1), ('bloco', 2), ('52_17', 3), ('personalizado', 4), ('sem_sessao', 5))
         AS g(grupo, ordem)
    LEFT JOIN agregado AS a USING (grupo)
    ORDER BY g.ordem
"""


def acerto_pos_sessao(session: Session, *, usuario_id: int, disciplina_id: int | None, de: date,
                      ate: date, janela_min: int) -> list[dict]:
    sql = SQL_ACERTO_POS_SESSAO.format(
        periodo=_periodo("r.respondido_em", de, ate), filtro=_filtro("r.disciplina_id", disciplina_id)
    )
    parametros = {"usuario_id": usuario_id, "disciplina_id": disciplina_id, "de": de, "ate": ate,
                  "janela_min": janela_min}
    return [dict(r) for r in session.execute(text(sql), parametros).mappings()]
