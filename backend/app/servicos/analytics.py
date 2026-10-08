"""Consultas analíticas (fase 5), em SQL explícito e comentado.

Cada consulta ensina um conceito; a explicação completa e o EXPLAIN de cada uma
estão em docs/analytics.md. Convenções:
- "dia" e "semana" são SEMPRE do fuso do usuário. vw_respostas já traz o dia
  local de cada resposta; o resto usa AT TIME ZONE usuarios.fuso_horario.
- Consultas sobre o HISTÓRICO leem a materialized view mv_respostas_diarias
  (rápido; dado do último REFRESH). Consultas sobre "agora" (sequência atual,
  previsão, ranking, custos) leem as tabelas ao vivo.
- Filtros opcionais entram como um trecho "AND ..." montado em Python (as
  colunas são constantes; os valores vão como parâmetros). Nunca
  "(:p IS NULL OR col = :p)", que confunde o planejador (fase 4).
- Taxa de acerto = soma de acertos / soma de respostas (razão das somas), nunca
  a média das taxas diárias: um dia com 1 resposta não pode pesar o mesmo que um
  dia com 80.
"""

import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

MV = "mv_respostas_diarias"


def _filtro(coluna: str, valor) -> str:
    return f"AND {coluna} = :disciplina_id" if valor is not None else ""


def _periodo(coluna: str, de: date | None, ate: date | None) -> str:
    """Filtro de período por DATAS LOCAIS sobre uma coluna timestamptz.

    Converte os LIMITES para instantes (meia-noite local de :de e de :ate + 1) e
    compara a coluna "crua". Escrever (coluna AT TIME ZONE fuso)::date >= :de dá o
    mesmo resultado, mas aplica uma função na COLUNA, e aí nenhum índice sobre ela
    pode ser usado (a condição deixa de ser "sargable"). Exige "u" = usuarios no FROM.
    """
    trecho = ""
    if de is not None:
        trecho += f" AND {coluna} >= (CAST(:de AS timestamp) AT TIME ZONE u.fuso_horario)"
    if ate is not None:
        trecho += f" AND {coluna} < (CAST(:ate AS timestamp) + interval '1 day') AT TIME ZONE u.fuso_horario"
    return trecho


def hoje_local(session: Session, usuario_id: int, agora: datetime | None = None) -> date:
    """A data de hoje no fuso do usuário (calculada no banco, com o tzdata dele)."""
    return session.execute(
        text("""
            SELECT (coalesce(CAST(:agora AS timestamptz), now()) AT TIME ZONE fuso_horario)::date
            FROM usuarios WHERE id = :usuario_id
        """),
        {"agora": agora, "usuario_id": usuario_id},
    ).scalar_one()


def atualizado_em(session: Session) -> datetime:
    return session.execute(
        text("SELECT atualizado_em FROM atualizacoes_mv WHERE nome = :nome"), {"nome": MV}
    ).scalar_one()


# ============================================================================
# 1. Taxa de acerto por semana: GROUP BY + date_trunc + GROUPING SETS + "densificar"
# ============================================================================
#
# - date_trunc('week', dia) leva cada dia para a segunda-feira da sua semana (ISO).
# - GROUPING SETS calcula DOIS agrupamentos numa passada: por (semana, disciplina,
#   fonte) e por (semana, disciplina) — este último soma revisões + questões.
#   GROUPING(fonte) = 1 marca as linhas do segundo agrupamento ("total").
# - "Densificar": semanas sem nenhuma resposta não existem em GROUP BY nenhum. O
#   produto cartesiano semanas x disciplinas x fontes (generate_series + CROSS JOIN)
#   + LEFT JOIN garante uma linha por combinação, com 0 respostas e taxa NULL.
#   Para um gráfico, uma semana vazia tem de aparecer como vazia, não sumir.
# - nullif(x, 0) evita divisão por zero: x / NULL = NULL.
# - sum() de bigint devolve numeric (para não estourar); ::int volta a inteiro.
SQL_ACERTO_SEMANAL_DISCIPLINA = """
    WITH agregado AS (
        SELECT date_trunc('week', m.dia)::date AS semana,
               m.disciplina_id,
               CASE WHEN GROUPING(m.fonte) = 1 THEN 'total' ELSE m.fonte END AS fonte,
               sum(m.respostas)::int AS respostas,
               sum(m.acertos)::int AS acertos
        FROM mv_respostas_diarias AS m
        WHERE m.usuario_id = :usuario_id
          AND m.dia BETWEEN :de AND :ate
          {filtro}
        GROUP BY GROUPING SETS (
            (date_trunc('week', m.dia), m.disciplina_id, m.fonte),
            (date_trunc('week', m.dia), m.disciplina_id)
        )
    ),
    semanas AS (
        SELECT generate_series(date_trunc('week', CAST(:de AS date)),
                               CAST(:ate AS date), interval '1 week')::date AS semana
    ),
    grade AS (  -- todas as combinações que DEVEM aparecer
        SELECT s.semana, d.id AS disciplina_id, d.nome AS disciplina, f.fonte
        FROM semanas AS s
        CROSS JOIN disciplinas AS d
        CROSS JOIN (VALUES ('revisao'), ('questao'), ('total')) AS f(fonte)
        WHERE d.usuario_id = :usuario_id {filtro_d}
    )
    SELECT g.semana, g.disciplina_id, g.disciplina AS grupo, g.fonte,
           coalesce(a.respostas, 0) AS respostas,
           coalesce(a.acertos, 0) AS acertos,
           round(a.acertos::numeric / nullif(a.respostas, 0), 4) AS taxa
    FROM grade AS g
    LEFT JOIN agregado AS a USING (semana, disciplina_id, fonte)
    ORDER BY g.semana, g.disciplina, g.fonte
"""

# Por MATERIAL: material não está na MV (a relação card/questão -> material é N:N,
# via flashcard_trechos/questao_trechos -> trechos). Consulta ao vivo.
# CUIDADO com o N:N: um card ligado a 2 trechos do MESMO material apareceria 2 vezes
# no JOIN e contaria a resposta em dobro. O SELECT DISTINCT (item, material) deixa
# um par por material. Um card ligado a 2 materiais DIFERENTES conta nos dois (é
# uma resposta sobre os dois), então a soma por material passa do total de respostas.
SQL_ACERTO_SEMANAL_MATERIAL = """
    WITH item_material AS (
        SELECT DISTINCT 'revisao' AS fonte, ft.flashcard_id AS item_id, t.material_id
        FROM flashcard_trechos AS ft JOIN trechos AS t ON t.id = ft.trecho_id
        UNION ALL
        SELECT DISTINCT 'questao', qt.questao_id, t.material_id
        FROM questao_trechos AS qt JOIN trechos AS t ON t.id = qt.trecho_id
    )
    SELECT date_trunc('week', r.dia)::date AS semana,
           r.disciplina_id,
           m.titulo AS grupo,
           'total' AS fonte,
           count(*) AS respostas,
           count(*) FILTER (WHERE r.acertou) AS acertos,
           round(count(*) FILTER (WHERE r.acertou)::numeric / count(*), 4) AS taxa
    FROM vw_respostas AS r
    JOIN item_material AS im ON im.fonte = r.fonte AND im.item_id = r.item_id
    JOIN materiais AS m ON m.id = im.material_id
    WHERE r.usuario_id = :usuario_id
      AND r.dia BETWEEN :de AND :ate
      {filtro}
    GROUP BY 1, 2, 3
    ORDER BY semana, grupo
"""


def acerto_semanal(session, *, usuario_id, disciplina_id, de, ate, por: str):
    params = {"usuario_id": usuario_id, "de": de, "ate": ate, "disciplina_id": disciplina_id}
    if por == "material":
        sql = SQL_ACERTO_SEMANAL_MATERIAL.format(filtro=_filtro("r.disciplina_id", disciplina_id))
    else:
        sql = SQL_ACERTO_SEMANAL_DISCIPLINA.format(
            filtro=_filtro("m.disciplina_id", disciplina_id),
            filtro_d=_filtro("d.id", disciplina_id),
        )
    return session.execute(text(sql), params).mappings().all()


# ============================================================================
# 2a. Evolução diária: média móvel de 7 dias com window function
# ============================================================================
#
# - A janela "ROWS BETWEEN 6 PRECEDING AND CURRENT ROW" soma a linha atual e as 6
#   anteriores. Isso só é "7 dias" se existir UMA linha por dia: por isso a série é
#   densificada com generate_series (dias sem estudo entram com 0).
#   (Alternativa sem densificar: RANGE BETWEEN interval '6 days' PRECEDING AND
#   CURRENT ROW, que olha a DISTÂNCIA no tempo, não o número de linhas.)
# - Média móvel da TAXA = soma móvel de acertos / soma móvel de respostas.
# - Janelas só enxergam as linhas que passaram pelo WHERE. Para o 1o dia pedido ter
#   7 dias de histórico na janela, a série começa 6 dias ANTES de :de e o corte
#   "dia >= :de" acontece numa consulta de fora, DEPOIS da janela.
SQL_EVOLUCAO_DIARIA = """
    WITH dias AS (
        SELECT generate_series(CAST(:de AS date) - 6, CAST(:ate AS date), interval '1 day')::date AS dia
    ),
    por_dia AS (
        SELECT m.dia, sum(m.respostas)::int AS respostas, sum(m.acertos)::int AS acertos
        FROM mv_respostas_diarias AS m
        WHERE m.usuario_id = :usuario_id
          AND m.dia BETWEEN CAST(:de AS date) - 6 AND :ate
          {filtro}
        GROUP BY m.dia
    ),
    serie AS (
        SELECT d.dia,
               coalesce(p.respostas, 0) AS respostas,
               coalesce(p.acertos, 0) AS acertos,
               sum(coalesce(p.respostas, 0)) OVER sete_dias AS respostas_7d,
               sum(coalesce(p.acertos, 0)) OVER sete_dias AS acertos_7d
        FROM dias AS d
        LEFT JOIN por_dia AS p USING (dia)
        WINDOW sete_dias AS (ORDER BY d.dia ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)
    )
    SELECT dia, respostas, acertos,
           round(acertos::numeric / nullif(respostas, 0), 4) AS taxa,
           respostas_7d,
           round(acertos_7d::numeric / nullif(respostas_7d, 0), 4) AS taxa_media_7d
    FROM serie
    WHERE dia >= :de          -- corte DEPOIS da janela
    ORDER BY dia
"""


def evolucao_diaria(session, *, usuario_id, disciplina_id, de, ate):
    sql = SQL_EVOLUCAO_DIARIA.format(filtro=_filtro("m.disciplina_id", disciplina_id))
    return session.execute(
        text(sql), {"usuario_id": usuario_id, "de": de, "ate": ate, "disciplina_id": disciplina_id}
    ).mappings().all()


# ============================================================================
# 2b. Evolução semanal: comparação com a semana anterior via LAG
# ============================================================================
#
# - LAG(x) OVER (ORDER BY semana) = o valor de x na linha ANTERIOR.
# - Mais uma vez a série é densificada: sem a semana vazia, o LAG da semana seguinte
#   compararia com DUAS semanas atrás, sem avisar.
# - variacao_pp = diferença em pontos percentuais (0,62 -> 0,70 = +8 pp), que é
#   diferente de variação percentual (+12,9%).
SQL_EVOLUCAO_SEMANAL = """
    WITH semanas AS (
        SELECT generate_series(date_trunc('week', CAST(:de AS date)),
                               CAST(:ate AS date), interval '1 week')::date AS semana
    ),
    por_semana AS (
        SELECT date_trunc('week', m.dia)::date AS semana,
               sum(m.respostas)::int AS respostas, sum(m.acertos)::int AS acertos
        FROM mv_respostas_diarias AS m
        WHERE m.usuario_id = :usuario_id
          AND m.dia BETWEEN date_trunc('week', CAST(:de AS date))::date AND :ate
          {filtro}
        GROUP BY 1
    ),
    serie AS (
        SELECT s.semana,
               coalesce(p.respostas, 0) AS respostas,
               coalesce(p.acertos, 0) AS acertos,
               round(p.acertos::numeric / nullif(p.respostas, 0), 4) AS taxa
        FROM semanas AS s LEFT JOIN por_semana AS p USING (semana)
    )
    SELECT semana, respostas, acertos, taxa,
           lag(taxa) OVER (ORDER BY semana) AS taxa_semana_anterior,
           round(100 * (taxa - lag(taxa) OVER (ORDER BY semana)), 2) AS variacao_pp,
           respostas - lag(respostas) OVER (ORDER BY semana) AS variacao_respostas
    FROM serie
    ORDER BY semana
"""


def evolucao_semanal(session, *, usuario_id, disciplina_id, de, ate):
    sql = SQL_EVOLUCAO_SEMANAL.format(filtro=_filtro("m.disciplina_id", disciplina_id))
    return session.execute(
        text(sql), {"usuario_id": usuario_id, "de": de, "ate": ate, "disciplina_id": disciplina_id}
    ).mappings().all()


# ============================================================================
# 3. Cards mais difíceis por disciplina: DENSE_RANK com PARTITION BY
# ============================================================================
#
# - PARTITION BY disciplina reinicia a contagem em cada disciplina: é um "top N
#   POR GRUPO", que GROUP BY + ORDER BY + LIMIT não resolve.
# - ROW_NUMBER: 1,2,3,4 (empates desempatados arbitrariamente).
#   RANK:       1,2,2,4 (empate divide a posição e PULA a seguinte).
#   DENSE_RANK: 1,2,2,3 (empate divide a posição, sem pular).
#   Com DENSE_RANK, "top 3" pode trazer mais de 3 cards se houver empate: os
#   empatados são igualmente difíceis, e cortar um deles seria arbitrário.
# - Funções de janela não podem aparecer no WHERE (são calculadas DEPOIS dele):
#   o filtro "posicao <= :limite" fica numa consulta de fora (o Postgres não
#   tem QUALIFY).
SQL_CARDS_DIFICEIS = """
    WITH estatisticas AS (
        SELECT f.disciplina_id, d.nome AS disciplina, f.id AS flashcard_id, f.frente, f.topico,
               count(*) AS revisoes,
               count(*) FILTER (WHERE h.nota < 3) AS erros,
               r.facilidade
        FROM historico_revisoes AS h
        JOIN flashcards AS f ON f.id = h.flashcard_id
        JOIN disciplinas AS d ON d.id = f.disciplina_id
        JOIN revisoes AS r ON r.flashcard_id = f.id
        JOIN usuarios AS u ON u.id = d.usuario_id
        WHERE d.usuario_id = :usuario_id
          {filtro}
          {periodo}
        GROUP BY f.disciplina_id, d.nome, f.id, f.frente, f.topico, r.facilidade
    ),
    ranqueado AS (
        SELECT *,
               dense_rank() OVER (PARTITION BY disciplina_id
                                  ORDER BY erros DESC, facilidade ASC) AS posicao,
               rank() OVER (PARTITION BY disciplina_id
                            ORDER BY erros DESC, facilidade ASC) AS posicao_rank
        FROM estatisticas
    )
    SELECT disciplina_id, disciplina, posicao, posicao_rank, flashcard_id, frente, topico,
           revisoes, erros, round(erros::numeric / revisoes, 4) AS taxa_erro, facilidade
    FROM ranqueado
    WHERE posicao <= :limite
    ORDER BY disciplina, posicao, flashcard_id
"""


def cards_dificeis(session, *, usuario_id, disciplina_id, de, ate, limite):
    sql = SQL_CARDS_DIFICEIS.format(
        filtro=_filtro("f.disciplina_id", disciplina_id), periodo=_periodo("h.revisado_em", de, ate)
    )
    return session.execute(
        text(sql),
        {"usuario_id": usuario_id, "disciplina_id": disciplina_id, "de": de, "ate": ate,
         "limite": limite},
    ).mappings().all()


# ============================================================================
# 4. Sequência de estudo (streak): gaps-and-islands com CTEs encadeadas
# ============================================================================
#
# Técnica "gaps and islands": em dias consecutivos, dia e row_number() sobem
# juntos (+1, +1, ...), então dia - row_number() fica CONSTANTE dentro de cada
# sequência e muda quando há um buraco. Essa diferença vira o identificador da
# "ilha":
#       dia         row_number   dia - row_number
#       2026-10-01      1          2026-09-30   <- ilha A
#       2026-10-02      2          2026-09-30   <- ilha A
#       2026-10-03      3          2026-09-30   <- ilha A
#       2026-10-06      4          2026-10-02   <- ilha B (buraco em 04 e 05)
#
# Cada CTE faz uma coisa (dias_estudados -> ilhas -> sequencias -> resumo), e a
# consulta se lê de cima para baixo. Ao vivo (não MV): "estudei agora" tem de
# contar na hora.
# Sequência ATUAL: a ilha que termina hoje OU ontem (se ainda não estudou hoje, a
# sequência continua viva até o fim do dia). Senão, 0.
SQL_SEQUENCIA = """
    WITH hoje AS (
        SELECT (coalesce(CAST(:agora AS timestamptz), now()) AT TIME ZONE fuso_horario)::date AS dia
        FROM usuarios WHERE id = :usuario_id
    ),
    dias_estudados AS (                         -- 1. um dia por linha, sem repetir
        SELECT DISTINCT r.dia
        FROM vw_respostas AS r
        WHERE r.usuario_id = :usuario_id AND r.fonte = 'revisao'
          {filtro}
    ),
    ilhas AS (                                  -- 2. dia - posição = id da ilha
        SELECT dia, dia - CAST(row_number() OVER (ORDER BY dia) AS integer) AS ilha
        FROM dias_estudados
    ),
    sequencias AS (                             -- 3. uma linha por sequência
        SELECT min(dia) AS inicio, max(dia) AS fim, count(*) AS dias
        FROM ilhas
        GROUP BY ilha
    ),
    atual AS (                                  -- 4. a que termina hoje ou ontem
        SELECT s.* FROM sequencias AS s, hoje AS h
        WHERE s.fim >= h.dia - 1
    ),
    maior AS (                                  -- 5. a mais longa (a mais recente, se empatar)
        SELECT * FROM sequencias ORDER BY dias DESC, fim DESC LIMIT 1
    )
    SELECT (SELECT dia FROM hoje) AS hoje,
           coalesce((SELECT dias FROM atual), 0) AS atual_dias,
           (SELECT inicio FROM atual) AS atual_inicio,
           (SELECT fim FROM atual) AS atual_fim,
           EXISTS (SELECT 1 FROM dias_estudados, hoje WHERE dias_estudados.dia = hoje.dia)
               AS estudou_hoje,
           coalesce((SELECT dias FROM maior), 0) AS maior_dias,
           (SELECT inicio FROM maior) AS maior_inicio,
           (SELECT fim FROM maior) AS maior_fim,
           (SELECT count(*) FROM dias_estudados) AS dias_estudados
"""


def sequencia(session, *, usuario_id, disciplina_id, agora=None):
    sql = SQL_SEQUENCIA.format(filtro=_filtro("r.disciplina_id", disciplina_id))
    return session.execute(
        text(sql), {"usuario_id": usuario_id, "disciplina_id": disciplina_id, "agora": agora}
    ).mappings().one()


# ============================================================================
# 5. Previsão de carga: generate_series para os dias sem nada vencendo
# ============================================================================
#
# - O GROUP BY só produz os dias em que ALGUM card vence. generate_series cria os
#   N dias e o LEFT JOIN completa com 0: o gráfico mostra o dia vazio como vazio.
# - Cards já atrasados (vencidos antes de hoje) entram em HOJE, porque é quando
#   serão revistos; ficam também separados na coluna "atrasados".
# - É uma previsão a partir do estado ATUAL: cada revisão feita reagenda o card,
#   então os dias mais distantes vão mudar.
SQL_PREVISAO = """
    WITH hoje AS (
        SELECT fuso_horario AS fuso,
               (coalesce(CAST(:agora AS timestamptz), now()) AT TIME ZONE fuso_horario)::date AS dia
        FROM usuarios WHERE id = :usuario_id
    ),
    dias AS (
        SELECT generate_series(h.dia, h.dia + :dias - 1, interval '1 day')::date AS dia
        FROM hoje AS h
    ),
    vencimentos AS (
        SELECT greatest((r.proxima_revisao AT TIME ZONE h.fuso)::date, h.dia) AS dia,
               count(*) AS cards,
               count(*) FILTER (WHERE (r.proxima_revisao AT TIME ZONE h.fuso)::date < h.dia)
                   AS atrasados
        FROM revisoes AS r
        JOIN disciplinas AS d ON d.id = r.disciplina_id
        CROSS JOIN hoje AS h
        WHERE d.usuario_id = :usuario_id
          {filtro}
        GROUP BY 1
    )
    SELECT d.dia, coalesce(v.cards, 0) AS cards, coalesce(v.atrasados, 0) AS atrasados
    FROM dias AS d
    LEFT JOIN vencimentos AS v USING (dia)
    ORDER BY d.dia
"""


def previsao(session, *, usuario_id, disciplina_id, dias, agora=None):
    sql = SQL_PREVISAO.format(filtro=_filtro("r.disciplina_id", disciplina_id))
    return session.execute(
        text(sql),
        {"usuario_id": usuario_id, "disciplina_id": disciplina_id, "dias": dias, "agora": agora},
    ).mappings().all()


# ============================================================================
# 6. Calendário de atividade (estilo GitHub): generate_series + percentis
# ============================================================================
#
# - Um ano de dias via generate_series + LEFT JOIN (dias sem revisão = 0).
# - O "nível" (0 a 4, a cor do quadradinho) é relativo ao próprio aluno: os cortes
#   são os quartis dos dias COM atividade, calculados com percentile_cont, um
#   "ordered-set aggregate" (WITHIN GROUP (ORDER BY ...)). Passar um ARRAY de
#   frações devolve vários percentis numa passada.
# - isodow (1 = segunda ... 7 = domingo) é a linha do quadradinho no calendário.
SQL_CALENDARIO = """
    WITH dias AS (
        SELECT generate_series(CAST(:de AS date), CAST(:ate AS date), interval '1 day')::date AS dia
    ),
    por_dia AS (
        SELECT m.dia, sum(m.respostas)::int AS revisoes
        FROM mv_respostas_diarias AS m
        WHERE m.usuario_id = :usuario_id AND m.fonte = 'revisao'
          AND m.dia BETWEEN :de AND :ate
          {filtro}
        GROUP BY m.dia
    ),
    serie AS (
        SELECT d.dia, coalesce(p.revisoes, 0) AS revisoes
        FROM dias AS d LEFT JOIN por_dia AS p USING (dia)
    ),
    cortes AS (
        SELECT percentile_cont(ARRAY[0.25, 0.5, 0.75]) WITHIN GROUP (ORDER BY revisoes) AS q
        FROM serie WHERE revisoes > 0
    )
    SELECT s.dia,
           extract(isodow FROM s.dia)::int AS dia_semana,
           s.revisoes,
           CASE WHEN s.revisoes = 0 THEN 0
                WHEN s.revisoes <= c.q[1] THEN 1
                WHEN s.revisoes <= c.q[2] THEN 2
                WHEN s.revisoes <= c.q[3] THEN 3
                ELSE 4 END AS nivel
    FROM serie AS s CROSS JOIN cortes AS c
    ORDER BY s.dia
"""


def calendario(session, *, usuario_id, disciplina_id, de, ate):
    sql = SQL_CALENDARIO.format(filtro=_filtro("m.disciplina_id", disciplina_id))
    return session.execute(
        text(sql), {"usuario_id": usuario_id, "disciplina_id": disciplina_id, "de": de, "ate": ate}
    ).mappings().all()


# ============================================================================
# 7. Custo de IA por mês e tipo, com total acumulado: SUM() OVER
# ============================================================================
#
# - Agregação normal (GROUP BY mês, tipo) numa CTE + DUAS janelas sobre o resultado:
#   * acumulado do tipo: sum(custo) OVER (PARTITION BY tipo ORDER BY mes);
#   * acumulado geral: sum(custo) OVER (ORDER BY mes).
#   (Sem a CTE daria para escrever sum(sum(g.custo_usd)) OVER (...) direto no
#   SELECT do GROUP BY: o sum() de dentro é o agregado, o de fora é a janela.)
# - O acumulado geral usa o quadro padrão "RANGE ... CURRENT ROW": linhas com o
#   MESMO mês (os outros tipos) são "pares" e entram juntas. Todas as linhas de
#   um mês mostram o acumulado até o fim daquele mês. Com ROWS, cada linha
#   somaria só até ela mesma, e o resultado dependeria da ordem dos tipos.
SQL_CUSTOS = """
    WITH por_mes AS (
        SELECT date_trunc('month', g.criado_em AT TIME ZONE u.fuso_horario)::date AS mes,
               g.tipo,
               count(*) AS geracoes,
               count(*) FILTER (WHERE g.status <> 'sucesso') AS falhas,
               sum(g.tokens_entrada) AS tokens_entrada,
               sum(g.tokens_saida) AS tokens_saida,
               coalesce(sum(g.custo_usd), 0) AS custo_usd
        FROM geracoes AS g
        JOIN usuarios AS u ON u.id = g.usuario_id
        WHERE g.usuario_id = :usuario_id
          {filtro}
          {periodo}
        GROUP BY 1, 2
    )
    SELECT mes, tipo, geracoes, falhas, tokens_entrada, tokens_saida, custo_usd,
           sum(custo_usd) OVER (PARTITION BY tipo ORDER BY mes) AS acumulado_tipo,
           sum(custo_usd) OVER (ORDER BY mes) AS acumulado_total
    FROM por_mes
    ORDER BY mes, tipo
"""


def custos(session, *, usuario_id, disciplina_id, de, ate):
    sql = SQL_CUSTOS.format(
        filtro=_filtro("g.disciplina_id", disciplina_id), periodo=_periodo("g.criado_em", de, ate)
    )
    return session.execute(
        text(sql), {"usuario_id": usuario_id, "disciplina_id": disciplina_id, "de": de, "ate": ate}
    ).mappings().all()


# ============================================================================
# 8. REFRESH da materialized view
# ============================================================================


@dataclass
class Atualizacao:
    atualizado_em: datetime
    duracao_ms: int


def atualizar_mv(session: Session) -> Atualizacao:
    """REFRESH ... CONCURRENTLY: recalcula e aplica só as diferenças, sem bloquear quem
    está lendo a MV (o refresh comum bloquearia até as leituras). Exige o índice
    único uq_mv_respostas_diarias."""
    inicio = time.perf_counter()
    session.execute(text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {MV}"))
    duracao = int((time.perf_counter() - inicio) * 1000)
    quando = session.execute(
        text("""UPDATE atualizacoes_mv SET atualizado_em = now(), duracao_ms = :d
                WHERE nome = :nome RETURNING atualizado_em"""),
        {"d": duracao, "nome": MV},
    ).scalar_one()
    session.commit()
    return Atualizacao(quando, duracao)


def periodo_padrao(hoje: date, dias: int) -> tuple[date, date]:
    return hoje - timedelta(days=dias - 1), hoje
