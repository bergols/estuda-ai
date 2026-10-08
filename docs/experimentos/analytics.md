# Experimento: performance do analytics

Gerado por `backend/scripts/experimento_analytics.py` (não edite à mão; rode o script de novo).

- 435,006 revisões e 26,946 tentativas (seed com 200 alunos); `historico_revisoes` com índices: 97 MB; `mv_respostas_diarias`: 44,825 linhas, 5592 kB.
- 20 alunos sorteados por medição, cache aquecido. Tempo = `Execution Time` do EXPLAIN ANALYZE em ms (mediana e p95). Buffers = páginas de 8 KB tocadas (1o aluno).
- Mudanças de esquema (índices) e REFRESH rodaram numa transação desfeita no fim.

## 1. Evolução diária (30 dias)

| Variante | Mediana | p95 | Buffers | Plano |
|---|---:|---:|---:|---|
| ao vivo (vw_respostas) | 1.79 | 1.90 | 1720 | `Subquery Scan → WindowAgg → Sort → Hash Join → Result → ProjectSet → Result → Hash → Subquery Scan → Aggregate → Append → Subquery Scan → Nested Loop → Nested Loop → Nested Loop → Seq Scan → Bitmap Heap Scan → Bitmap Index Scan (uq_disciplinas_usuario_nome) → Index Scan (ix_flashcards_disciplina_id) → Index Only Scan (ix_historico_revisoes_flashcard_revisado_em) → Subquery Scan → Nested Loop → Nested Loop → Nested Loop → Seq Scan → Bitmap Heap Scan → Bitmap Index Scan (uq_disciplinas_usuario_nome) → Index Scan (ix_questoes_disciplina_id) → Index Scan (ix_tentativas_questao_respondida_em)` |
| materialized view | 0.12 | 0.13 | 122 | `Subquery Scan → WindowAgg → Sort → Hash Join → Result → ProjectSet → Result → Hash → Subquery Scan → Aggregate → Bitmap Heap Scan → Bitmap Index Scan (uq_mv_respostas_diarias)` |

## 1. Calendário (365 dias)

| Variante | Mediana | p95 | Buffers | Plano |
|---|---:|---:|---:|---|
| ao vivo (vw_respostas) | 1.86 | 2.78 | 1499 | `Sort → Hash Join → Result → ProjectSet → Result → Hash → Subquery Scan → Aggregate → Subquery Scan → Nested Loop → Nested Loop → Nested Loop → Seq Scan → Bitmap Heap Scan → Bitmap Index Scan (uq_disciplinas_usuario_nome) → Index Scan (ix_flashcards_disciplina_id) → Index Only Scan (ix_historico_revisoes_flashcard_revisado_em) → Nested Loop → Aggregate → CTE Scan → CTE Scan` |
| materialized view | 0.28 | 0.29 | 97 | `Sort → Hash Join → Result → ProjectSet → Result → Hash → Subquery Scan → Aggregate → Bitmap Heap Scan → Bitmap Index Scan (uq_mv_respostas_diarias) → Nested Loop → Aggregate → CTE Scan → CTE Scan` |

## 2. CTE com o filtro do lado de fora

| Variante | Mediana | p95 | Buffers | Plano |
|---|---:|---:|---:|---|
| AS NOT MATERIALIZED (padrão quando referenciada 1 vez) | 1.47 | 1.57 | 1641 | `Aggregate → Append → Subquery Scan → Nested Loop → Nested Loop → Nested Loop → Seq Scan → Bitmap Heap Scan → Bitmap Index Scan (uq_disciplinas_usuario_nome) → Index Scan (ix_flashcards_disciplina_id) → Index Only Scan (ix_historico_revisoes_flashcard_revisado_em) → Subquery Scan → Nested Loop → Nested Loop → Nested Loop → Seq Scan → Bitmap Heap Scan → Bitmap Index Scan (uq_disciplinas_usuario_nome) → Index Scan (ix_questoes_disciplina_id) → Index Only Scan (ix_tentativas_questao_respondida_em)` |
| AS MATERIALIZED | 332.46 | 382.37 | 9083 | `Aggregate → Append → Hash Join → Hash Join → Hash Join → Seq Scan → Hash → Seq Scan → Hash → Seq Scan → Hash → Seq Scan → Hash Join → Hash Join → Hash Join → Seq Scan → Hash → Seq Scan → Hash → Seq Scan → Hash → Seq Scan → CTE Scan` |

## 3. Filtro de período (7 dias)

| Variante | Mediana | p95 | Buffers | Plano |
|---|---:|---:|---:|---|
| não sargable: (coluna AT TIME ZONE ...)::date | 0.82 | 0.87 | 1499 | `Aggregate → Nested Loop → Nested Loop → Nested Loop → Seq Scan → Bitmap Heap Scan → Bitmap Index Scan (uq_disciplinas_usuario_nome) → Index Scan (ix_flashcards_disciplina_id) → Index Only Scan (ix_historico_revisoes_flashcard_revisado_em)` |
| coluna crua, limite vindo do JOIN (u.fuso_horario) | 0.73 | 0.76 | 1499 | `Aggregate → Nested Loop → Nested Loop → Nested Loop → Seq Scan → Bitmap Heap Scan → Bitmap Index Scan (uq_disciplinas_usuario_nome) → Index Scan (ix_flashcards_disciplina_id) → Index Only Scan (ix_historico_revisoes_flashcard_revisado_em)` |
| sargable: coluna crua + limite em subconsulta (InitPlan) | 0.46 | 0.47 | 1500 | `Aggregate → Seq Scan → Seq Scan → Nested Loop → Nested Loop → Nested Loop → Seq Scan → Bitmap Heap Scan → Bitmap Index Scan (uq_disciplinas_usuario_nome) → Index Scan (ix_flashcards_disciplina_id) → Index Only Scan (ix_historico_revisoes_flashcard_revisado_em)` |

## 6. Acerto semanal por material (7 semanas)

| Variante | Mediana | p95 | Buffers | Plano |
|---|---:|---:|---:|---|
| antes: DISTINCT sobre as associações de todos os alunos | 77.04 | 83.50 | 69406 | `Aggregate → Gather Merge → Aggregate → Sort → Nested Loop → Hash Join → Append → Unique → Incremental Sort → Nested Loop → Index Only Scan (pk_flashcard_trechos) → Memoize → Index Scan (pk_trechos) → Aggregate → Hash Join → Seq Scan → Hash → Seq Scan → Hash → Append → Subquery Scan → Nested Loop → Nested Loop → Nested Loop → Seq Scan → Bitmap Heap Scan → Bitmap Index Scan (uq_disciplinas_usuario_nome) → Index Scan (ix_flashcards_disciplina_id) → Index Only Scan (ix_historico_revisoes_flashcard_revisado_em) → Subquery Scan → Nested Loop → Nested Loop → Nested Loop → Seq Scan → Bitmap Heap Scan → Bitmap Index Scan (uq_disciplinas_usuario_nome) → Index Scan (ix_questoes_disciplina_id) → Index Scan (ix_tentativas_questao_respondida_em) → Index Scan (pk_materiais)` |
| depois: só os itens das disciplinas do aluno | 5.29 | 5.72 | 9513 | `Aggregate → Bitmap Heap Scan → Bitmap Index Scan (uq_disciplinas_usuario_nome) → Sort → Nested Loop → Hash Join → Append → Aggregate → Nested Loop → Nested Loop → Aggregate → CTE Scan → Index Scan (ix_trechos_disciplina_id) → Index Scan (ix_flashcard_trechos_trecho_id) → Unique → Sort → Nested Loop → Nested Loop → Aggregate → CTE Scan → Index Scan (ix_trechos_disciplina_id) → Index Scan (ix_questao_trechos_trecho_id) → Hash → Append → Subquery Scan → Nested Loop → Nested Loop → Nested Loop → Seq Scan → Bitmap Heap Scan → Bitmap Index Scan (uq_disciplinas_usuario_nome) → Index Scan (ix_flashcards_disciplina_id) → Index Only Scan (ix_historico_revisoes_flashcard_revisado_em) → Subquery Scan → Nested Loop → Nested Loop → Nested Loop → Seq Scan → Bitmap Heap Scan → Bitmap Index Scan (uq_disciplinas_usuario_nome) → Index Scan (ix_questoes_disciplina_id) → Index Scan (ix_tentativas_questao_respondida_em) → Index Scan (pk_materiais)` |

## 4. Índice com INCLUDE (index-only scan)

| Consulta | Índice | Mediana | p95 | Buffers | Plano |
|---|---|---:|---:|---:|---|
| sequência | antes: (flashcard_id, revisado_em) | 1.29 | 1.41 | 1500 | `Result → Seq Scan → Aggregate → Subquery Scan → Nested Loop → Nested Loop → Nested Loop → Seq Scan → Bitmap Heap Scan → Bitmap Index Scan (uq_disciplinas_usuario_nome) → Index Scan (ix_flashcards_disciplina_id) → Index Only Scan (ix_historico_revisoes_flashcard_revisado_em) → Aggregate → WindowAgg → Sort → CTE Scan → Nested Loop → CTE Scan → CTE Scan → Limit → Sort → CTE Scan → CTE Scan → CTE Scan → CTE Scan → CTE Scan → Hash Join → CTE Scan → Hash → CTE Scan → CTE Scan → CTE Scan → CTE Scan → Aggregate → CTE Scan` |
| sequência | depois: + INCLUDE (nota) | 1.27 | 1.36 | 1503 | `Result → Seq Scan → Aggregate → Subquery Scan → Nested Loop → Nested Loop → Nested Loop → Seq Scan → Bitmap Heap Scan → Bitmap Index Scan (uq_disciplinas_usuario_nome) → Index Scan (ix_flashcards_disciplina_id) → Index Only Scan (ix_historico_revisoes_flashcard_revisado_em) → Aggregate → WindowAgg → Sort → CTE Scan → Nested Loop → CTE Scan → CTE Scan → Limit → Sort → CTE Scan → CTE Scan → CTE Scan → CTE Scan → CTE Scan → Hash Join → CTE Scan → Hash → CTE Scan → CTE Scan → CTE Scan → CTE Scan → Aggregate → CTE Scan` |
| cards difíceis | antes: (flashcard_id, revisado_em) | 1.70 | 1.80 | 5282 | `Sort → Subquery Scan → WindowAgg → Sort → Subquery Scan → Aggregate → Nested Loop → Nested Loop → Nested Loop → Nested Loop → Seq Scan → Bitmap Heap Scan → Bitmap Index Scan (uq_disciplinas_usuario_nome) → Index Scan (ix_flashcards_disciplina_id) → Index Scan (pk_revisoes) → Index Scan (ix_historico_revisoes_flashcard_revisado_em)` |
| cards difíceis | depois: + INCLUDE (nota) | 1.32 | 1.38 | 3419 | `Sort → Subquery Scan → WindowAgg → Sort → Subquery Scan → Aggregate → Nested Loop → Nested Loop → Nested Loop → Nested Loop → Seq Scan → Bitmap Heap Scan → Bitmap Index Scan (uq_disciplinas_usuario_nome) → Index Scan (ix_flashcards_disciplina_id) → Index Scan (pk_revisoes) → Index Only Scan (ix_historico_revisoes_flashcard_revisado_em)` |

## 5. REFRESH da materialized view

| Modo | Mediana (3 execuções) |
|---|---:|
| `REFRESH MATERIALIZED VIEW ` | 168 ms |
| `REFRESH MATERIALIZED VIEW CONCURRENTLY ` | 411 ms |

## Planos completos (EXPLAIN ANALYZE, 1o aluno)

<details><summary>1. Evolução diária (30 dias) — ao vivo (vw_respostas)</summary>

```
Subquery Scan on serie (actual time=1.486..1.502 rows=30 loops=1)
  Filter: (serie.dia >= '...'::date)
  Rows Removed by Filter: 6
  Buffers: shared hit=1720
  ->  WindowAgg (actual time=1.484..1.494 rows=36 loops=1)
        Buffers: shared hit=1720
        ->  Sort (actual time=1.483..1.485 rows=36 loops=1)
              Sort Key: (((generate_series(('...'::date)::timestamp with time zone, ('...'::date)::timestamp with time zone, '1 day'::interval)))::date)
              Sort Method: quicksort  Memory: 26kB
              Buffers: shared hit=1720
              ->  Hash Left Join (actual time=1.472..1.482 rows=36 loops=1)
                    Hash Cond: ((((generate_series(('...'::date)::timestamp with time zone, ('...'::date)::timestamp with time zone, '1 day'::interval)))::date) = p.dia)
                    Buffers: shared hit=1720
                    ->  Result (actual time=0.001..0.007 rows=36 loops=1)
                          ->  ProjectSet (actual time=0.000..0.004 rows=36 loops=1)
                                ->  Result (actual time=0.000..0.000 rows=1 loops=1)
                    ->  Hash (actual time=1.471..1.472 rows=23 loops=1)
                          Buckets: 1024  Batches: 1  Memory Usage: 9kB
                          Buffers: shared hit=1720
                          ->  Subquery Scan on p (actual time=1.467..1.471 rows=23 loops=1)
                                Buffers: shared hit=1720
                                ->  HashAggregate (actual time=1.467..1.469 rows=23 loops=1)
                                      Group Key: "*SELECT* 1".dia
                                      Batches: 1  Memory Usage: 40kB
                                      Buffers: shared hit=1720
                                      ->  Append (actual time=0.009..1.357 rows=1550 loops=1)
                                            Buffers: shared hit=1720
                                            ->  Subquery Scan on "*SELECT* 1" (actual time=0.009..1.192 rows=1452 loops=1)
                                                  Buffers: shared hit=1499
                                                  ->  Nested Loop (actual time=0.009..1.126 rows=1452 loops=1)
                                                        Join Filter: ((((h.revisado_em AT TIME ZONE u.fuso_horario))::date >= '...'::date) AND (((h.revisado_em AT TIME ZONE u.fuso_horario))::date <= '...'::date))
                                                        Rows Removed by Join Filter: 437
                                                        Buffers: shared hit=1499
                                                        ->  Nested Loop (actual time=0.006..0.069 rows=480 loops=1)
                                                              Buffers: shared hit=51
                                                              ->  Nested Loop (actual time=0.005..0.008 rows=4 loops=1)
                                                                    Buffers: shared hit=7
                                                                    ->  Seq Scan on usuarios u (actual time=0.003..0.005 rows=1 loops=1)
                                                                          Filter: (id = 1164)
                                                                          Rows Removed by Filter: 201
                                                                          Buffers: shared hit=4
                                                                    ->  Bitmap Heap Scan on disciplinas d (actual time=0.001..0.001 rows=4 loops=1)
                                                                          Recheck Cond: (usuario_id = 1164)
                                                                          Heap Blocks: exact=1
                                                                          Buffers: shared hit=3
                                                                          ->  Bitmap Index Scan on uq_disciplinas_usuario_nome (actual time=0.001..0.001 rows=4 loops=1)
                                                                                Index Cond: (usuario_id = 1164)
                                                                                Buffers: shared hit=2
                                                              ->  Index Scan using ix_flashcards_disciplina_id on flashcards f (actual time=0.001..0.010 rows=120 loops=4)
                                                                    Index Cond: (disciplina_id = d.id)
                                                                    Buffers: shared hit=44
                                                        ->  Index Only Scan using ix_historico_revisoes_flashcard_revisado_em on historico_revisoes h (actual time=0.000..0.001 rows=4 loops=480)
                                                              Index Cond: (flashcard_id = f.id)
                                                              Heap Fetches: 0
                                                              Buffers: shared hit=1448
                                            ->  Subquery Scan on "*SELECT* 2" (actual time=0.007..0.107 rows=98 loops=1)
                                                  Buffers: shared hit=221
                                                  ->  Nested Loop (actual time=0.007..0.103 rows=98 loops=1)
                                                        Join Filter: ((((t.respondida_em AT TIME ZONE u_1.fuso_horario))::date >= '...'::date) AND (((t.respondida_em AT TIME ZONE u_1.fuso_horario))::date <= '...'::date))
                                                        Rows Removed by Join Filter: 17
                                                        Buffers: shared hit=221
                                                        ->  Nested Loop (actual time=0.005..0.016 rows=60 loops=1)
                                                              Buffers: shared hit=21
                                                              ->  Nested Loop (actual time=0.004..0.007 rows=4 loops=1)
                                                                    Buffers: shared hit=7
                                                                    ->  Seq Scan on usuarios u_1 (actual time=0.003..0.005 rows=1 loops=1)
                                                                          Filter: (id = 1164)
                                                                          Rows Removed by Filter: 201
                                                                          Buffers: shared hit=4
                                                                    ->  Bitmap Heap Scan on disciplinas d_1 (actual time=0.001..0.001 rows=4 loops=1)
                                                                          Recheck Cond: (usuario_id = 1164)
                                                                          Heap Blocks: exact=1
                                                                          Buffers: shared hit=3
                                                                          ->  Bitmap Index Scan on uq_disciplinas_usuario_nome (actual time=0.000..0.001 rows=4 loops=1)
                                                                                Index Cond: (usuario_id = 1164)
                                                                                Buffers: shared hit=2
                                                              ->  Index Scan using ix_questoes_disciplina_id on questoes q (actual time=0.001..0.002 rows=15 loops=4)
                                                                    Index Cond: (disciplina_id = d_1.id)
                                                                    Buffers: shared hit=14
                                                        ->  Index Scan using ix_tentativas_questao_respondida_em on tentativas t (actual time=0.000..0.001 rows=2 loops=60)
                                                              Index Cond: (questao_id = q.id)
                                                              Buffers: shared hit=200
Planning:
  Buffers: shared hit=46
Planning Time: 0.263 ms
Execution Time: 1.521 ms
```
</details>

<details><summary>1. Evolução diária (30 dias) — materialized view</summary>

```
Subquery Scan on serie (actual time=0.075..0.090 rows=30 loops=1)
  Filter: (serie.dia >= '...'::date)
  Rows Removed by Filter: 6
  Buffers: shared hit=122
  ->  WindowAgg (actual time=0.073..0.082 rows=36 loops=1)
        Buffers: shared hit=122
        ->  Sort (actual time=0.072..0.073 rows=36 loops=1)
              Sort Key: (((generate_series(('...'::date)::timestamp with time zone, ('...'::date)::timestamp with time zone, '1 day'::interval)))::date)
              Sort Method: quicksort  Memory: 26kB
              Buffers: shared hit=122
              ->  Hash Left Join (actual time=0.062..0.070 rows=36 loops=1)
                    Hash Cond: ((((generate_series(('...'::date)::timestamp with time zone, ('...'::date)::timestamp with time zone, '1 day'::interval)))::date) = p.dia)
                    Buffers: shared hit=122
                    ->  Result (actual time=0.000..0.006 rows=36 loops=1)
                          ->  ProjectSet (actual time=0.000..0.003 rows=36 loops=1)
                                ->  Result (actual time=0.000..0.000 rows=1 loops=1)
                    ->  Hash (actual time=0.061..0.061 rows=23 loops=1)
                          Buckets: 1024  Batches: 1  Memory Usage: 9kB
                          Buffers: shared hit=122
                          ->  Subquery Scan on p (actual time=0.055..0.059 rows=23 loops=1)
                                Buffers: shared hit=122
                                ->  HashAggregate (actual time=0.055..0.058 rows=23 loops=1)
                                      Group Key: m.dia
                                      Batches: 1  Memory Usage: 24kB
                                      Buffers: shared hit=122
                                      ->  Bitmap Heap Scan on mv_respostas_diarias m (actual time=0.011..0.041 rows=153 loops=1)
                                            Recheck Cond: ((usuario_id = 1164) AND (dia >= '...'::date) AND (dia <= '...'::date))
                                            Heap Blocks: exact=118
                                            Buffers: shared hit=122
                                            ->  Bitmap Index Scan on uq_mv_respostas_diarias (actual time=0.006..0.006 rows=153 loops=1)
                                                  Index Cond: ((usuario_id = 1164) AND (dia >= '...'::date) AND (dia <= '...'::date))
                                                  Buffers: shared hit=4
Planning Time: 0.036 ms
Execution Time: 0.100 ms
```
</details>

<details><summary>1. Calendário (365 dias) — ao vivo (vw_respostas)</summary>

```
Sort (actual time=1.647..1.656 rows=365 loops=1)
  Sort Key: s.dia
  Sort Method: quicksort  Memory: 39kB
  Buffers: shared hit=1499
  CTE serie
    ->  Hash Left Join (actual time=1.462..1.548 rows=365 loops=1)
          Hash Cond: ((((generate_series(('...'::date)::timestamp with time zone, ('...'::date)::timestamp with time zone, '1 day'::interval)))::date) = p.dia)
          Buffers: shared hit=1499
          ->  Result (actual time=0.001..0.061 rows=365 loops=1)
                ->  ProjectSet (actual time=0.001..0.029 rows=365 loops=1)
                      ->  Result (actual time=0.000..0.000 rows=1 loops=1)
          ->  Hash (actual time=1.460..1.461 rows=29 loops=1)
                Buckets: 1024  Batches: 1  Memory Usage: 10kB
                Buffers: shared hit=1499
                ->  Subquery Scan on p (actual time=1.454..1.459 rows=29 loops=1)
                      Buffers: shared hit=1499
                      ->  HashAggregate (actual time=1.454..1.457 rows=29 loops=1)
                            Group Key: "*SELECT* 1".dia
                            Batches: 1  Memory Usage: 40kB
                            Buffers: shared hit=1499
                            ->  Subquery Scan on "*SELECT* 1" (actual time=0.012..1.334 rows=1889 loops=1)
                                  Buffers: shared hit=1499
                                  ->  Nested Loop (actual time=0.012..1.252 rows=1889 loops=1)
                                        Join Filter: ((((h.revisado_em AT TIME ZONE u.fuso_horario))::date >= '...'::date) AND (((h.revisado_em AT TIME ZONE u.fuso_horario))::date <= '...'::date))
                                        Buffers: shared hit=1499
                                        ->  Nested Loop (actual time=0.009..0.069 rows=480 loops=1)
                                              Buffers: shared hit=51
                                              ->  Nested Loop (actual time=0.006..0.010 rows=4 loops=1)
                                                    Buffers: shared hit=7
                                                    ->  Seq Scan on usuarios u (actual time=0.004..0.007 rows=1 loops=1)
                                                          Filter: (id = 1164)
                                                          Rows Removed by Filter: 201
                                                          Buffers: shared hit=4
                                                    ->  Bitmap Heap Scan on disciplinas d (actual time=0.002..0.002 rows=4 loops=1)
                                                          Recheck Cond: (usuario_id = 1164)
                                                          Heap Blocks: exact=1
                                                          Buffers: shared hit=3
                                                          ->  Bitmap Index Scan on uq_disciplinas_usuario_nome (actual time=0.001..0.001 rows=4 loops=1)
                                                                Index Cond: (usuario_id = 1164)
                                                                Buffers: shared hit=2
                                              ->  Index Scan using ix_flashcards_disciplina_id on flashcards f (actual time=0.001..0.009 rows=120 loops=4)
                                                    Index Cond: (disciplina_id = d.id)
                                                    Buffers: shared hit=44
                                        ->  Index Only Scan using ix_historico_revisoes_flashcard_revisado_em on historico_revisoes h (actual time=0.000..0.001 rows=4 loops=480)
                                              Index Cond: (flashcard_id = f.id)
                                              Heap Fetches: 0
                                              Buffers: shared hit=1448
  ->  Nested Loop (actual time=1.580..1.629 rows=365 loops=1)
        Buffers: shared hit=1499
        ->  Aggregate (actual time=1.578..1.578 rows=1 loops=1)
              Buffers: shared hit=1499
              ->  CTE Scan on serie (actual time=1.559..1.574 rows=29 loops=1)
                    Filter: (revisoes > 0)
                    Rows Removed by Filter: 336
                    Buffers: shared hit=1499
        ->  CTE Scan on serie s (actual time=0.000..0.011 rows=365 loops=1)
Planning:
  Buffers: shared hit=58
Planning Time: 0.368 ms
Execution Time: 1.683 ms
```
</details>

<details><summary>1. Calendário (365 dias) — materialized view</summary>

```
Sort (actual time=0.231..0.240 rows=365 loops=1)
  Sort Key: s.dia
  Sort Method: quicksort  Memory: 39kB
  Buffers: shared hit=97
  CTE serie
    ->  Hash Left Join (actual time=0.050..0.135 rows=365 loops=1)
          Hash Cond: ((((generate_series(('...'::date)::timestamp with time zone, ('...'::date)::timestamp with time zone, '1 day'::interval)))::date) = p.dia)
          Buffers: shared hit=97
          ->  Result (actual time=0.001..0.060 rows=365 loops=1)
                ->  ProjectSet (actual time=0.000..0.029 rows=365 loops=1)
                      ->  Result (actual time=0.000..0.000 rows=1 loops=1)
          ->  Hash (actual time=0.049..0.049 rows=29 loops=1)
                Buckets: 1024  Batches: 1  Memory Usage: 10kB
                Buffers: shared hit=97
                ->  Subquery Scan on p (actual time=0.044..0.048 rows=29 loops=1)
                      Buffers: shared hit=97
                      ->  HashAggregate (actual time=0.044..0.046 rows=29 loops=1)
                            Group Key: m.dia
                            Batches: 1  Memory Usage: 24kB
                            Buffers: shared hit=97
                            ->  Bitmap Heap Scan on mv_respostas_diarias m (actual time=0.011..0.034 rows=115 loops=1)
                                  Recheck Cond: ((usuario_id = 1164) AND (dia >= '...'::date) AND (dia <= '...'::date) AND (fonte = 'revisao'::text))
                                  Heap Blocks: exact=93
                                  Buffers: shared hit=97
                                  ->  Bitmap Index Scan on uq_mv_respostas_diarias (actual time=0.007..0.007 rows=115 loops=1)
                                        Index Cond: ((usuario_id = 1164) AND (dia >= '...'::date) AND (dia <= '...'::date) AND (fonte = 'revisao'::text))
                                        Buffers: shared hit=4
  ->  Nested Loop (actual time=0.164..0.214 rows=365 loops=1)
        Buffers: shared hit=97
        ->  Aggregate (actual time=0.163..0.163 rows=1 loops=1)
              Buffers: shared hit=97
              ->  CTE Scan on serie (actual time=0.146..0.161 rows=29 loops=1)
                    Filter: (revisoes > 0)
                    Rows Removed by Filter: 336
                    Buffers: shared hit=97
        ->  CTE Scan on serie s (actual time=0.000..0.011 rows=365 loops=1)
Planning Time: 0.039 ms
Execution Time: 0.259 ms
```
</details>

<details><summary>2. CTE com o filtro do lado de fora — AS NOT MATERIALIZED (padrão quando referenciada 1 vez)</summary>

```
HashAggregate (actual time=1.224..1.227 rows=29 loops=1)
  Group Key: "*SELECT* 1".dia
  Batches: 1  Memory Usage: 40kB
  Buffers: shared hit=1641
  ->  Append (actual time=0.011..1.087 rows=2004 loops=1)
        Buffers: shared hit=1641
        ->  Subquery Scan on "*SELECT* 1" (actual time=0.011..0.928 rows=1889 loops=1)
              Buffers: shared hit=1499
              ->  Nested Loop (actual time=0.011..0.836 rows=1889 loops=1)
                    Buffers: shared hit=1499
                    ->  Nested Loop (actual time=0.008..0.078 rows=480 loops=1)
                          Buffers: shared hit=51
                          ->  Nested Loop (actual time=0.006..0.009 rows=4 loops=1)
                                Buffers: shared hit=7
                                ->  Seq Scan on usuarios u (actual time=0.004..0.006 rows=1 loops=1)
                                      Filter: (id = 1164)
                                      Rows Removed by Filter: 201
                                      Buffers: shared hit=4
                                ->  Bitmap Heap Scan on disciplinas d (actual time=0.001..0.002 rows=4 loops=1)
                                      Recheck Cond: (usuario_id = 1164)
                                      Heap Blocks: exact=1
                                      Buffers: shared hit=3
                                      ->  Bitmap Index Scan on uq_disciplinas_usuario_nome (actual time=0.001..0.001 rows=4 loops=1)
                                            Index Cond: (usuario_id = 1164)
                                            Buffers: shared hit=2
                          ->  Index Scan using ix_flashcards_disciplina_id on flashcards f (actual time=0.001..0.011 rows=120 loops=4)
                                Index Cond: (disciplina_id = d.id)
                                Buffers: shared hit=44
                    ->  Index Only Scan using ix_historico_revisoes_flashcard_revisado_em on historico_revisoes h (actual time=0.000..0.001 rows=4 loops=480)
                          Index Cond: (flashcard_id = f.id)
                          Heap Fetches: 0
                          Buffers: shared hit=1448
        ->  Subquery Scan on "*SELECT* 2" (actual time=0.007..0.079 rows=115 loops=1)
              Buffers: shared hit=142
              ->  Nested Loop (actual time=0.007..0.073 rows=115 loops=1)
                    Buffers: shared hit=142
                    ->  Nested Loop (actual time=0.005..0.017 rows=60 loops=1)
                          Buffers: shared hit=21
                          ->  Nested Loop (actual time=0.004..0.007 rows=4 loops=1)
                                Buffers: shared hit=7
                                ->  Seq Scan on usuarios u_1 (actual time=0.003..0.005 rows=1 loops=1)
                                      Filter: (id = 1164)
                                      Rows Removed by Filter: 201
                                      Buffers: shared hit=4
                                ->  Bitmap Heap Scan on disciplinas d_1 (actual time=0.001..0.001 rows=4 loops=1)
                                      Recheck Cond: (usuario_id = 1164)
                                      Heap Blocks: exact=1
                                      Buffers: shared hit=3
                                      ->  Bitmap Index Scan on uq_disciplinas_usuario_nome (actual time=0.001..0.001 rows=4 loops=1)
                                            Index Cond: (usuario_id = 1164)
                                            Buffers: shared hit=2
                          ->  Index Scan using ix_questoes_disciplina_id on questoes q (actual time=0.001..0.002 rows=15 loops=4)
                                Index Cond: (disciplina_id = d_1.id)
                                Buffers: shared hit=14
                    ->  Index Only Scan using ix_tentativas_questao_respondida_em on tentativas t (actual time=0.000..0.000 rows=2 loops=60)
                          Index Cond: (questao_id = q.id)
                          Heap Fetches: 0
                          Buffers: shared hit=121
Planning:
  Buffers: shared hit=46
Planning Time: 0.198 ms
Execution Time: 1.240 ms
```
</details>

<details><summary>2. CTE com o filtro do lado de fora — AS MATERIALIZED</summary>

```
HashAggregate (actual time=314.694..314.701 rows=29 loops=1)
  Group Key: respostas.dia
  Batches: 1  Memory Usage: 40kB
  Buffers: shared hit=9083, temp written=3496
  CTE respostas
    ->  Append (actual time=11.433..252.723 rows=461952 loops=1)
          Buffers: shared hit=9083
          ->  Hash Join (actual time=11.432..221.453 rows=435006 loops=1)
                Hash Cond: (d.usuario_id = u.id)
                Buffers: shared hit=8043
                ->  Hash Join (actual time=11.401..117.586 rows=435006 loops=1)
                      Hash Cond: (f.disciplina_id = d.id)
                      Buffers: shared hit=8039
                      ->  Hash Join (actual time=11.312..82.175 rows=435006 loops=1)
                            Hash Cond: (h.flashcard_id = f.id)
                            Buffers: shared hit=8023
                            ->  Seq Scan on historico_revisoes h (actual time=0.002..27.504 rows=435006 loops=1)
                                  Buffers: shared hit=6215
                            ->  Hash (actual time=11.186..11.187 rows=96000 loops=1)
                                  Buckets: 131072  Batches: 1  Memory Usage: 5524kB
                                  Buffers: shared hit=1808
                                  ->  Seq Scan on flashcards f (actual time=0.001..5.411 rows=96000 loops=1)
                                        Buffers: shared hit=1808
                      ->  Hash (actual time=0.088..0.088 rows=822 loops=1)
                            Buckets: 1024  Batches: 1  Memory Usage: 47kB
                            Buffers: shared hit=16
                            ->  Seq Scan on disciplinas d (actual time=0.001..0.045 rows=822 loops=1)
                                  Buffers: shared hit=16
                ->  Hash (actual time=0.029..0.029 rows=202 loops=1)
                      Buckets: 1024  Batches: 1  Memory Usage: 21kB
                      Buffers: shared hit=4
                      ->  Seq Scan on usuarios u (actual time=0.002..0.016 rows=202 loops=1)
                            Buffers: shared hit=4
          ->  Hash Join (actual time=1.404..13.405 rows=26946 loops=1)
                Hash Cond: (d_1.usuario_id = u_1.id)
                Buffers: shared hit=1040
                ->  Hash Join (actual time=1.367..7.268 rows=26946 loops=1)
                      Hash Cond: (q.disciplina_id = d_1.id)
                      Buffers: shared hit=1036
                      ->  Hash Join (actual time=1.290..5.079 rows=26946 loops=1)
                            Hash Cond: (t.questao_id = q.id)
                            Buffers: shared hit=1020
                            ->  Seq Scan on tentativas t (actual time=0.007..1.426 rows=26946 loops=1)
                                  Buffers: shared hit=502
                            ->  Hash (actual time=1.268..1.269 rows=12000 loops=1)
                                  Buckets: 16384  Batches: 1  Memory Usage: 691kB
                                  Buffers: shared hit=518
                                  ->  Seq Scan on questoes q (actual time=0.002..0.667 rows=12000 loops=1)
                                        Buffers: shared hit=518
                      ->  Hash (actual time=0.075..0.076 rows=822 loops=1)
                            Buckets: 1024  Batches: 1  Memory Usage: 47kB
                            Buffers: shared hit=16
                            ->  Seq Scan on disciplinas d_1 (actual time=0.002..0.039 rows=822 loops=1)
                                  Buffers: shared hit=16
                ->  Hash (actual time=0.028..0.028 rows=202 loops=1)
                      Buckets: 1024  Batches: 1  Memory Usage: 21kB
                      Buffers: shared hit=4
                      ->  Seq Scan on usuarios u_1 (actual time=0.005..0.017 rows=202 loops=1)
                            Buffers: shared hit=4
  ->  CTE Scan on respostas (actual time=173.490..314.517 rows=2004 loops=1)
        Filter: (usuario_id = 1164)
        Rows Removed by Filter: 459948
        Buffers: shared hit=9083, temp written=3496
Planning:
  Buffers: shared hit=70
Planning Time: 0.346 ms
Execution Time: 316.075 ms
```
</details>

<details><summary>3. Filtro de período (7 dias) — não sargable: (coluna AT TIME ZONE ...)::date</summary>

```
Aggregate (actual time=0.714..0.714 rows=1 loops=1)
  Buffers: shared hit=1499
  ->  Nested Loop (actual time=0.014..0.710 rows=124 loops=1)
        Join Filter: ((((h.revisado_em AT TIME ZONE u.fuso_horario))::date >= '...'::date) AND (((h.revisado_em AT TIME ZONE u.fuso_horario))::date <= '...'::date))
        Rows Removed by Join Filter: 1765
        Buffers: shared hit=1499
        ->  Nested Loop (actual time=0.005..0.066 rows=480 loops=1)
              Buffers: shared hit=51
              ->  Nested Loop (actual time=0.004..0.007 rows=4 loops=1)
                    Buffers: shared hit=7
                    ->  Seq Scan on usuarios u (actual time=0.003..0.005 rows=1 loops=1)
                          Filter: (id = 1164)
                          Rows Removed by Filter: 201
                          Buffers: shared hit=4
                    ->  Bitmap Heap Scan on disciplinas d (actual time=0.001..0.001 rows=4 loops=1)
                          Recheck Cond: (usuario_id = 1164)
                          Heap Blocks: exact=1
                          Buffers: shared hit=3
                          ->  Bitmap Index Scan on uq_disciplinas_usuario_nome (actual time=0.001..0.001 rows=4 loops=1)
                                Index Cond: (usuario_id = 1164)
                                Buffers: shared hit=2
              ->  Index Scan using ix_flashcards_disciplina_id on flashcards f (actual time=0.001..0.009 rows=120 loops=4)
                    Index Cond: (disciplina_id = d.id)
                    Buffers: shared hit=44
        ->  Index Only Scan using ix_historico_revisoes_flashcard_revisado_em on historico_revisoes h (actual time=0.000..0.001 rows=4 loops=480)
              Index Cond: (flashcard_id = f.id)
              Heap Fetches: 0
              Buffers: shared hit=1448
Planning:
  Buffers: shared hit=25
Planning Time: 0.078 ms
Execution Time: 0.719 ms
```
</details>

<details><summary>3. Filtro de período (7 dias) — coluna crua, limite vindo do JOIN (u.fuso_horario)</summary>

```
Aggregate (actual time=0.630..0.630 rows=1 loops=1)
  Buffers: shared hit=1499
  ->  Nested Loop (actual time=0.014..0.627 rows=124 loops=1)
        Join Filter: ((h.revisado_em >= ('...'::timestamp without time zone AT TIME ZONE u.fuso_horario)) AND (h.revisado_em < ('...'::timestamp without time zone AT TIME ZONE u.fuso_horario)))
        Rows Removed by Join Filter: 1765
        Buffers: shared hit=1499
        ->  Nested Loop (actual time=0.006..0.064 rows=480 loops=1)
              Buffers: shared hit=51
              ->  Nested Loop (actual time=0.005..0.007 rows=4 loops=1)
                    Buffers: shared hit=7
                    ->  Seq Scan on usuarios u (actual time=0.003..0.005 rows=1 loops=1)
                          Filter: (id = 1164)
                          Rows Removed by Filter: 201
                          Buffers: shared hit=4
                    ->  Bitmap Heap Scan on disciplinas d (actual time=0.001..0.001 rows=4 loops=1)
                          Recheck Cond: (usuario_id = 1164)
                          Heap Blocks: exact=1
                          Buffers: shared hit=3
                          ->  Bitmap Index Scan on uq_disciplinas_usuario_nome (actual time=0.001..0.001 rows=4 loops=1)
                                Index Cond: (usuario_id = 1164)
                                Buffers: shared hit=2
              ->  Index Scan using ix_flashcards_disciplina_id on flashcards f (actual time=0.001..0.009 rows=120 loops=4)
                    Index Cond: (disciplina_id = d.id)
                    Buffers: shared hit=44
        ->  Index Only Scan using ix_historico_revisoes_flashcard_revisado_em on historico_revisoes h (actual time=0.000..0.001 rows=4 loops=480)
              Index Cond: (flashcard_id = f.id)
              Heap Fetches: 0
              Buffers: shared hit=1448
Planning:
  Buffers: shared hit=25
Planning Time: 0.105 ms
Execution Time: 0.635 ms
```
</details>

<details><summary>3. Filtro de período (7 dias) — sargable: coluna crua + limite em subconsulta (InitPlan)</summary>

```
Aggregate (actual time=0.419..0.420 rows=1 loops=1)
  Buffers: shared hit=1500
  InitPlan 1 (returns $0)
    ->  Seq Scan on usuarios (actual time=0.003..0.005 rows=1 loops=1)
          Filter: (id = 1164)
          Rows Removed by Filter: 201
          Buffers: shared hit=4
  InitPlan 2 (returns $1)
    ->  Seq Scan on usuarios usuarios_1 (actual time=0.002..0.004 rows=1 loops=1)
          Filter: (id = 1164)
          Rows Removed by Filter: 201
          Buffers: shared hit=4
  ->  Nested Loop (actual time=0.019..0.415 rows=124 loops=1)
        Buffers: shared hit=1500
        ->  Nested Loop (actual time=0.005..0.062 rows=480 loops=1)
              Buffers: shared hit=51
              ->  Nested Loop (actual time=0.004..0.007 rows=4 loops=1)
                    Buffers: shared hit=7
                    ->  Seq Scan on usuarios u (actual time=0.003..0.005 rows=1 loops=1)
                          Filter: (id = 1164)
                          Rows Removed by Filter: 201
                          Buffers: shared hit=4
                    ->  Bitmap Heap Scan on disciplinas d (actual time=0.001..0.001 rows=4 loops=1)
                          Recheck Cond: (usuario_id = 1164)
                          Heap Blocks: exact=1
                          Buffers: shared hit=3
                          ->  Bitmap Index Scan on uq_disciplinas_usuario_nome (actual time=0.001..0.001 rows=4 loops=1)
                                Index Cond: (usuario_id = 1164)
                                Buffers: shared hit=2
              ->  Index Scan using ix_flashcards_disciplina_id on flashcards f (actual time=0.001..0.008 rows=120 loops=4)
                    Index Cond: (disciplina_id = d.id)
                    Buffers: shared hit=44
        ->  Index Only Scan using ix_historico_revisoes_flashcard_revisado_em on historico_revisoes h (actual time=0.000..0.000 rows=0 loops=480)
              Index Cond: ((flashcard_id = f.id) AND (revisado_em >= ('...'::timestamp without time zone AT TIME ZONE $0)) AND (revisado_em < ('...'::timestamp without time zone AT TIME ZONE $1)))
              Heap Fetches: 0
              Buffers: shared hit=1441
Planning:
  Buffers: shared hit=25
Planning Time: 0.082 ms
Execution Time: 0.425 ms
```
</details>

<details><summary>6. Acerto semanal por material (7 semanas) — antes: DISTINCT sobre as associações de todos os alunos</summary>

```
Finalize GroupAggregate (actual time=77.812..79.170 rows=120 loops=1)
  Group Key: ((date_trunc('week'::text, ("*SELECT* 1".dia)::timestamp with time zone))::date), m.titulo, "*SELECT* 1".disciplina_id
  Buffers: shared hit=62367 read=7039
  ->  Gather Merge (actual time=77.808..79.130 rows=196 loops=1)
        Workers Planned: 2
        Workers Launched: 2
        Buffers: shared hit=62367 read=7039
        ->  Partial GroupAggregate (actual time=38.306..38.396 rows=65 loops=3)
              Group Key: ((date_trunc('week'::text, ("*SELECT* 1".dia)::timestamp with time zone))::date), m.titulo, "*SELECT* 1".disciplina_id
              Buffers: shared hit=62367 read=7039
              ->  Sort (actual time=38.303..38.329 rows=786 loops=3)
                    Sort Key: ((date_trunc('week'::text, ("*SELECT* 1".dia)::timestamp with time zone))::date), m.titulo, "*SELECT* 1".disciplina_id
                    Sort Method: quicksort  Memory: 32kB
                    Buffers: shared hit=62367 read=7039
                    Worker 0:  Sort Method: quicksort  Memory: 244kB
                    Worker 1:  Sort Method: quicksort  Memory: 25kB
                    ->  Nested Loop (actual time=25.343..37.933 rows=786 loops=3)
                          Buffers: shared hit=62319 read=7039
                          ->  Parallel Hash Join (actual time=25.335..37.452 rows=786 loops=3)
                                Hash Cond: ((('revisao'::text) = "*SELECT* 1".fonte) AND (ft.flashcard_id = "*SELECT* 1".item_id))
                                Buffers: shared hit=55244 read=7039
                                ->  Parallel Append (actual time=11.840..33.660 rows=42350 loops=3)
                                      Buffers: shared hit=53490 read=7039
                                      ->  Unique (actual time=0.085..59.043 rows=115051 loops=1)
                                            Buffers: shared hit=48471
                                            ->  Incremental Sort (actual time=0.084..50.325 rows=115051 loops=1)
                                                  Sort Key: ft.flashcard_id, t.material_id
                                                  Presorted Key: ft.flashcard_id
                                                  Buffers: shared hit=48471
                                                  Worker 0:  Full-sort Groups: 3577  Sort Method: quicksort  Average Memory: 26kB  Peak Memory: 26kB
                                                  ->  Nested Loop (actual time=0.028..38.813 rows=115051 loops=1)
                                                        Buffers: shared hit=48447
                                                        ->  Index Only Scan using pk_flashcard_trechos on flashcard_trechos ft (actual time=0.018..5.668 rows=115051 loops=1)
                                                              Heap Fetches: 0
                                                              Buffers: shared hit=446
                                                        ->  Memoize (actual time=0.000..0.000 rows=1 loops=115051)
                                                              Cache Key: ft.trecho_id
                                                              Cache Mode: logical
                                                              Buffers: shared hit=48001
                                                              Worker 0:  Hits: 99051  Misses: 16000  Evictions: 0  Overflows: 0  Memory Usage: 1875kB
                                                              ->  Index Scan using pk_trechos on trechos t (actual time=0.001..0.001 rows=1 loops=16000)
                                                                    Index Cond: (id = ft.trecho_id)
                                                                    Buffers: shared hit=48001
                                      ->  HashAggregate (actual time=35.435..36.422 rows=12000 loops=1)
                                            Group Key: qt.questao_id, t_1.material_id
                                            Batches: 1  Memory Usage: 1425kB
                                            Buffers: shared hit=5019 read=7039
                                            ->  Hash Join (actual time=0.877..32.929 rows=12000 loops=1)
                                                  Hash Cond: (t_1.id = qt.trecho_id)
                                                  Buffers: shared hit=5019 read=7039
                                                  ->  Seq Scan on trechos t_1 (actual time=0.002..20.627 rows=66006 loops=1)
                                                        Buffers: shared hit=4954 read=7039
                                                  ->  Hash (actual time=0.871..0.871 rows=12000 loops=1)
                                                        Buckets: 16384  Batches: 1  Memory Usage: 691kB
                                                        Buffers: shared hit=65
                                                        ->  Seq Scan on questao_trechos qt (actual time=0.001..0.359 rows=12000 loops=1)
                                                              Buffers: shared hit=65
                                ->  Parallel Hash (actual time=0.611..0.613 rows=668 loops=3)
                                      Buckets: 2048 (originally 1024)  Batches: 1 (originally 1)  Memory Usage: 184kB
                                      Buffers: shared hit=1720
                                      ->  Parallel Append (actual time=0.020..1.649 rows=2004 loops=1)
                                            Buffers: shared hit=1720
                                            ->  Subquery Scan on "*SELECT* 1" (actual time=0.016..1.428 rows=1889 loops=1)
                                                  Buffers: shared hit=1499
                                                  ->  Nested Loop (actual time=0.016..1.339 rows=1889 loops=1)
                                                        Join Filter: ((((h.revisado_em AT TIME ZONE u.fuso_horario))::date >= '...'::date) AND (((h.revisado_em AT TIME ZONE u.fuso_horario))::date <= '...'::date))
                                                        Buffers: shared hit=1499
                                                        ->  Nested Loop (actual time=0.007..0.088 rows=480 loops=1)
                                                              Buffers: shared hit=51
                                                              ->  Nested Loop (actual time=0.004..0.008 rows=4 loops=1)
                                                                    Buffers: shared hit=7
                                                                    ->  Seq Scan on usuarios u (actual time=0.003..0.005 rows=1 loops=1)
                                                                          Filter: (id = 1164)
                                                                          Rows Removed by Filter: 201
                                                                          Buffers: shared hit=4
                                                                    ->  Bitmap Heap Scan on disciplinas d (actual time=0.001..0.001 rows=4 loops=1)
                                                                          Recheck Cond: (usuario_id = 1164)
                                                                          Heap Blocks: exact=1
                                                                          Buffers: shared hit=3
                                                                          ->  Bitmap Index Scan on uq_disciplinas_usuario_nome (actual time=0.001..0.001 rows=4 loops=1)
                                                                                Index Cond: (usuario_id = 1164)
                                                                                Buffers: shared hit=2
                                                              ->  Index Scan using ix_flashcards_disciplina_id on flashcards f (actual time=0.001..0.014 rows=120 loops=4)
                                                                    Index Cond: (disciplina_id = d.id)
                                                                    Buffers: shared hit=44
                                                        ->  Index Only Scan using ix_historico_revisoes_flashcard_revisado_em on historico_revisoes h (actual time=0.000..0.001 rows=4 loops=480)
                                                              Index Cond: (flashcard_id = f.id)
                                                              Heap Fetches: 0
                                                              Buffers: shared hit=1448
                                            ->  Subquery Scan on "*SELECT* 2" (actual time=0.019..0.141 rows=115 loops=1)
                                                  Buffers: shared hit=221
                                                  ->  Nested Loop (actual time=0.019..0.134 rows=115 loops=1)
                                                        Join Filter: ((((t_2.respondida_em AT TIME ZONE u_1.fuso_horario))::date >= '...'::date) AND (((t_2.respondida_em AT TIME ZONE u_1.fuso_horario))::date <= '...'::date))
                                                        Buffers: shared hit=221
                                                        ->  Nested Loop (actual time=0.014..0.029 rows=60 loops=1)
                                                              Buffers: shared hit=21
                                                              ->  Nested Loop (actual time=0.010..0.015 rows=4 loops=1)
                                                                    Buffers: shared hit=7
                                                                    ->  Seq Scan on usuarios u_1 (actual time=0.006..0.009 rows=1 loops=1)
                                                                          Filter: (id = 1164)
                                                                          Rows Removed by Filter: 201
                                                                          Buffers: shared hit=4
                                                                    ->  Bitmap Heap Scan on disciplinas d_1 (actual time=0.004..0.004 rows=4 loops=1)
                                                                          Recheck Cond: (usuario_id = 1164)
                                                                          Heap Blocks: exact=1
                                                                          Buffers: shared hit=3
                                                                          ->  Bitmap Index Scan on uq_disciplinas_usuario_nome (actual time=0.002..0.002 rows=4 loops=1)
                                                                                Index Cond: (usuario_id = 1164)
                                                                                Buffers: shared hit=2
                                                              ->  Index Scan using ix_questoes_disciplina_id on questoes q (actual time=0.001..0.003 rows=15 loops=4)
                                                                    Index Cond: (disciplina_id = d_1.id)
                                                                    Buffers: shared hit=14
                                                        ->  Index Scan using ix_tentativas_questao_respondida_em on tentativas t_2 (actual time=0.000..0.001 rows=2 loops=60)
                                                              Index Cond: (questao_id = q.id)
                                                              Buffers: shared hit=200
                          ->  Index Scan using pk_materiais on materiais m (actual time=0.000..0.000 rows=1 loops=2358)
                                Index Cond: (id = t.material_id)
                                Buffers: shared hit=7075
Planning:
  Buffers: shared hit=64
Planning Time: 0.458 ms
Execution Time: 79.226 ms
```
</details>

<details><summary>6. Acerto semanal por material (7 semanas) — depois: só os itens das disciplinas do aluno</summary>

```
GroupAggregate (actual time=4.363..4.580 rows=120 loops=1)
  Group Key: ((date_trunc('week'::text, ("*SELECT* 1".dia)::timestamp with time zone))::date), m.titulo, "*SELECT* 1".disciplina_id
  Buffers: shared hit=9513
  CTE disciplinas_do_aluno
    ->  Bitmap Heap Scan on disciplinas (actual time=0.001..0.001 rows=4 loops=1)
          Recheck Cond: (usuario_id = 1164)
          Heap Blocks: exact=1
          Buffers: shared hit=3
          ->  Bitmap Index Scan on uq_disciplinas_usuario_nome (actual time=0.001..0.001 rows=4 loops=1)
                Index Cond: (usuario_id = 1164)
                Buffers: shared hit=2
  ->  Sort (actual time=4.361..4.420 rows=2358 loops=1)
        Sort Key: ((date_trunc('week'::text, ("*SELECT* 1".dia)::timestamp with time zone))::date), m.titulo, "*SELECT* 1".disciplina_id
        Sort Method: quicksort  Memory: 251kB
        Buffers: shared hit=9513
        ->  Nested Loop (actual time=1.883..3.468 rows=2358 loops=1)
              Buffers: shared hit=9513
              ->  Hash Join (actual time=1.881..2.198 rows=2358 loops=1)
                    Hash Cond: ((('revisao'::text) = "*SELECT* 1".fonte) AND (ft.flashcard_id = "*SELECT* 1".item_id))
                    Buffers: shared hit=2439
                    ->  Append (actual time=0.171..0.294 rows=624 loops=1)
                          Buffers: shared hit=719
                          ->  HashAggregate (actual time=0.171..0.204 rows=564 loops=1)
                                Group Key: ft.flashcard_id, t.material_id
                                Batches: 1  Memory Usage: 105kB
                                Buffers: shared hit=466
                                ->  Nested Loop (actual time=0.005..0.125 rows=564 loops=1)
                                      Buffers: shared hit=466
                                      ->  Nested Loop (actual time=0.004..0.020 rows=80 loops=1)
                                            Buffers: shared hit=36
                                            ->  HashAggregate (actual time=0.002..0.003 rows=4 loops=1)
                                                  Group Key: disciplinas_do_aluno.id
                                                  Batches: 1  Memory Usage: 24kB
                                                  Buffers: shared hit=3
                                                  ->  CTE Scan on disciplinas_do_aluno (actual time=0.001..0.002 rows=4 loops=1)
                                                        Buffers: shared hit=3
                                            ->  Index Scan using ix_trechos_disciplina_id on trechos t (actual time=0.001..0.003 rows=20 loops=4)
                                                  Index Cond: (disciplina_id = disciplinas_do_aluno.id)
                                                  Buffers: shared hit=33
                                      ->  Index Scan using ix_flashcard_trechos_trecho_id on flashcard_trechos ft (actual time=0.000..0.001 rows=7 loops=80)
                                            Index Cond: (trecho_id = t.id)
                                            Buffers: shared hit=430
                          ->  Unique (actual time=0.055..0.061 rows=60 loops=1)
                                Buffers: shared hit=253
                                ->  Sort (actual time=0.055..0.056 rows=60 loops=1)
                                      Sort Key: qt.questao_id, t_1.material_id
                                      Sort Method: quicksort  Memory: 27kB
                                      Buffers: shared hit=253
                                      ->  Nested Loop (actual time=0.003..0.050 rows=60 loops=1)
                                            Buffers: shared hit=253
                                            ->  Nested Loop (actual time=0.002..0.013 rows=80 loops=1)
                                                  Buffers: shared hit=33
                                                  ->  HashAggregate (actual time=0.001..0.001 rows=4 loops=1)
                                                        Group Key: disciplinas_do_aluno_1.id
                                                        Batches: 1  Memory Usage: 24kB
                                                        ->  CTE Scan on disciplinas_do_aluno disciplinas_do_aluno_1 (actual time=0.000..0.000 rows=4 loops=1)
                                                  ->  Index Scan using ix_trechos_disciplina_id on trechos t_1 (actual time=0.000..0.002 rows=20 loops=4)
                                                        Index Cond: (disciplina_id = disciplinas_do_aluno_1.id)
                                                        Buffers: shared hit=33
                                            ->  Index Scan using ix_questao_trechos_trecho_id on questao_trechos qt (actual time=0.000..0.000 rows=1 loops=80)
                                                  Index Cond: (trecho_id = t_1.id)
                                                  Buffers: shared hit=220
                    ->  Hash (actual time=1.709..1.710 rows=2004 loops=1)
                          Buckets: 2048 (originally 1024)  Batches: 1 (originally 1)  Memory Usage: 157kB
                          Buffers: shared hit=1720
                          ->  Append (actual time=0.009..1.545 rows=2004 loops=1)
                                Buffers: shared hit=1720
                                ->  Subquery Scan on "*SELECT* 1" (actual time=0.009..1.350 rows=1889 loops=1)
                                      Buffers: shared hit=1499
                                      ->  Nested Loop (actual time=0.009..1.260 rows=1889 loops=1)
                                            Join Filter: ((((h.revisado_em AT TIME ZONE u.fuso_horario))::date >= '...'::date) AND (((h.revisado_em AT TIME ZONE u.fuso_horario))::date <= '...'::date))
                                            Buffers: shared hit=1499
                                            ->  Nested Loop (actual time=0.006..0.074 rows=480 loops=1)
                                                  Buffers: shared hit=51
                                                  ->  Nested Loop (actual time=0.005..0.008 rows=4 loops=1)
                                                        Buffers: shared hit=7
                                                        ->  Seq Scan on usuarios u (actual time=0.003..0.005 rows=1 loops=1)
                                                              Filter: (id = 1164)
                                                              Rows Removed by Filter: 201
                                                              Buffers: shared hit=4
                                                        ->  Bitmap Heap Scan on disciplinas d (actual time=0.001..0.002 rows=4 loops=1)
                                                              Recheck Cond: (usuario_id = 1164)
                                                              Heap Blocks: exact=1
                                                              Buffers: shared hit=3
                                                              ->  Bitmap Index Scan on uq_disciplinas_usuario_nome (actual time=0.001..0.001 rows=4 loops=1)
                                                                    Index Cond: (usuario_id = 1164)
                                                                    Buffers: shared hit=2
                                                  ->  Index Scan using ix_flashcards_disciplina_id on flashcards f (actual time=0.001..0.011 rows=120 loops=4)
                                                        Index Cond: (disciplina_id = d.id)
                                                        Buffers: shared hit=44
                                            ->  Index Only Scan using ix_historico_revisoes_flashcard_revisado_em on historico_revisoes h (actual time=0.000..0.001 rows=4 loops=480)
                                                  Index Cond: (flashcard_id = f.id)
                                                  Heap Fetches: 0
                                                  Buffers: shared hit=1448
                                ->  Subquery Scan on "*SELECT* 2" (actual time=0.008..0.120 rows=115 loops=1)
                                      Buffers: shared hit=221
                                      ->  Nested Loop (actual time=0.008..0.110 rows=115 loops=1)
                                            Join Filter: ((((t_2.respondida_em AT TIME ZONE u_1.fuso_horario))::date >= '...'::date) AND (((t_2.respondida_em AT TIME ZONE u_1.fuso_horario))::date <= '...'::date))
                                            Buffers: shared hit=221
                                            ->  Nested Loop (actual time=0.005..0.018 rows=60 loops=1)
                                                  Buffers: shared hit=21
                                                  ->  Nested Loop (actual time=0.004..0.007 rows=4 loops=1)
                                                        Buffers: shared hit=7
                                                        ->  Seq Scan on usuarios u_1 (actual time=0.003..0.005 rows=1 loops=1)
                                                              Filter: (id = 1164)
                                                              Rows Removed by Filter: 201
                                                              Buffers: shared hit=4
                                                        ->  Bitmap Heap Scan on disciplinas d_1 (actual time=0.001..0.001 rows=4 loops=1)
                                                              Recheck Cond: (usuario_id = 1164)
                                                              Heap Blocks: exact=1
                                                              Buffers: shared hit=3
                                                              ->  Bitmap Index Scan on uq_disciplinas_usuario_nome (actual time=0.000..0.001 rows=4 loops=1)
                                                                    Index Cond: (usuario_id = 1164)
                                                                    Buffers: shared hit=2
                                                  ->  Index Scan using ix_questoes_disciplina_id on questoes q (actual time=0.001..0.002 rows=15 loops=4)
                                                        Index Cond: (disciplina_id = d_1.id)
                                                        Buffers: shared hit=14
                                            ->  Index Scan using ix_tentativas_questao_respondida_em on tentativas t_2 (actual time=0.000..0.001 rows=2 loops=60)
                                                  Index Cond: (questao_id = q.id)
                                                  Buffers: shared hit=200
              ->  Index Scan using pk_materiais on materiais m (actual time=0.000..0.000 rows=1 loops=2358)
                    Index Cond: (id = t.material_id)
                    Buffers: shared hit=7074
Planning:
  Buffers: shared hit=64
Planning Time: 0.318 ms
Execution Time: 4.607 ms
```
</details>

<details><summary>4. sequência — antes: (flashcard_id, revisado_em)</summary>

```
Result (actual time=1.163..1.164 rows=1 loops=1)
  Buffers: shared hit=1500
  CTE hoje
    ->  Seq Scan on usuarios (actual time=0.005..0.008 rows=1 loops=1)
          Filter: (id = 1164)
          Rows Removed by Filter: 201
          Buffers: shared hit=4
  CTE dias_estudados
    ->  HashAggregate (actual time=1.130..1.133 rows=29 loops=1)
          Group Key: "*SELECT* 1".dia
          Batches: 1  Memory Usage: 40kB
          Buffers: shared hit=1496
          ->  Subquery Scan on "*SELECT* 1" (actual time=0.011..1.002 rows=1889 loops=1)
                Buffers: shared hit=1496
                ->  Nested Loop (actual time=0.011..0.887 rows=1889 loops=1)
                      Buffers: shared hit=1496
                      ->  Nested Loop (actual time=0.008..0.083 rows=480 loops=1)
                            Buffers: shared hit=51
                            ->  Nested Loop (actual time=0.005..0.008 rows=4 loops=1)
                                  Buffers: shared hit=7
                                  ->  Seq Scan on usuarios u (actual time=0.003..0.005 rows=1 loops=1)
                                        Filter: (id = 1164)
                                        Rows Removed by Filter: 201
                                        Buffers: shared hit=4
                                  ->  Bitmap Heap Scan on disciplinas d (actual time=0.002..0.002 rows=4 loops=1)
                                        Recheck Cond: (usuario_id = 1164)
                                        Heap Blocks: exact=1
                                        Buffers: shared hit=3
                                        ->  Bitmap Index Scan on uq_disciplinas_usuario_nome (actual time=0.001..0.001 rows=4 loops=1)
                                              Index Cond: (usuario_id = 1164)
                                              Buffers: shared hit=2
                            ->  Index Scan using ix_flashcards_disciplina_id on flashcards f (actual time=0.001..0.012 rows=120 loops=4)
                                  Index Cond: (disciplina_id = d.id)
                                  Buffers: shared hit=44
                      ->  Index Only Scan using ix_historico_revisoes_flashcard_revisado_em on historico_revisoes h (actual time=0.000..0.001 rows=4 loops=480)
                            Index Cond: (flashcard_id = f.id)
                            Heap Fetches: 0
                            Buffers: shared hit=1445
  CTE sequencias
    ->  HashAggregate (actual time=1.144..1.145 rows=8 loops=1)
          Group Key: (dias_estudados.dia - (row_number() OVER (?))::integer)
          Batches: 1  Memory Usage: 40kB
          Buffers: shared hit=1496
          ->  WindowAgg (actual time=1.137..1.141 rows=29 loops=1)
                Buffers: shared hit=1496
                ->  Sort (actual time=1.136..1.137 rows=29 loops=1)
                      Sort Key: dias_estudados.dia
                      Sort Method: quicksort  Memory: 25kB
                      Buffers: shared hit=1496
                      ->  CTE Scan on dias_estudados (actual time=1.130..1.134 rows=29 loops=1)
                            Buffers: shared hit=1496
  CTE atual
    ->  Nested Loop (actual time=1.147..1.147 rows=0 loops=1)
          Join Filter: (s.fim >= (h_1.dia - 1))
          Rows Removed by Join Filter: 8
          Buffers: shared hit=1496
          ->  CTE Scan on hoje h_1 (actual time=0.000..0.000 rows=1 loops=1)
          ->  CTE Scan on sequencias s (actual time=1.144..1.146 rows=8 loops=1)
                Buffers: shared hit=1496
  CTE maior
    ->  Limit (actual time=0.002..0.002 rows=1 loops=1)
          ->  Sort (actual time=0.001..0.002 rows=1 loops=1)
                Sort Key: sequencias.dias DESC, sequencias.fim DESC
                Sort Method: top-N heapsort  Memory: 25kB
                ->  CTE Scan on sequencias (actual time=0.000..0.000 rows=8 loops=1)
  InitPlan 6 (returns $7)
    ->  CTE Scan on hoje (actual time=0.005..0.008 rows=1 loops=1)
          Buffers: shared hit=4
  InitPlan 7 (returns $8)
    ->  CTE Scan on atual (actual time=1.147..1.147 rows=0 loops=1)
          Buffers: shared hit=1496
  InitPlan 8 (returns $9)
    ->  CTE Scan on atual atual_1 (actual time=0.000..0.000 rows=0 loops=1)
  InitPlan 9 (returns $10)
    ->  CTE Scan on atual atual_2 (actual time=0.000..0.000 rows=0 loops=1)
  InitPlan 10 (returns $11)
    ->  Hash Join (actual time=0.003..0.003 rows=0 loops=1)
          Hash Cond: (dias_estudados_1.dia = hoje_1.dia)
          ->  CTE Scan on dias_estudados dias_estudados_1 (actual time=0.000..0.001 rows=29 loops=1)
          ->  Hash (actual time=0.000..0.000 rows=1 loops=1)
                Buckets: 1024  Batches: 1  Memory Usage: 9kB
                ->  CTE Scan on hoje hoje_1 (actual time=0.000..0.000 rows=1 loops=1)
  InitPlan 11 (returns $12)
    ->  CTE Scan on maior (actual time=0.002..0.002 rows=1 loops=1)
  InitPlan 12 (returns $13)
    ->  CTE Scan on maior maior_1 (actual time=0.000..0.000 rows=1 loops=1)
  InitPlan 13 (returns $14)
    ->  CTE Scan on maior maior_2 (actual time=0.000..0.000 rows=1 loops=1)
  InitPlan 14 (returns $15)
    ->  Aggregate (actual time=0.002..0.002 rows=1 loops=1)
          ->  CTE Scan on dias_estudados dias_estudados_2 (actual time=0.000..0.001 rows=29 loops=1)
Planning:
  Buffers: shared hit=58
Planning Time: 0.288 ms
Execution Time: 1.184 ms
```
</details>

<details><summary>4. cards difíceis — antes: (flashcard_id, revisado_em)</summary>

```
Sort (actual time=1.450..1.452 rows=54 loops=1)
  Sort Key: ranqueado.disciplina, ranqueado.posicao, ranqueado.flashcard_id
  Sort Method: quicksort  Memory: 32kB
  Buffers: shared hit=5282
  ->  Subquery Scan on ranqueado (actual time=1.384..1.436 rows=54 loops=1)
        Buffers: shared hit=5282
        ->  WindowAgg (actual time=1.384..1.427 rows=54 loops=1)
              Run Condition: (dense_rank() OVER (?) <= 5)
              Buffers: shared hit=5282
              ->  Sort (actual time=1.383..1.396 rows=480 loops=1)
                    Sort Key: estatisticas.disciplina_id, estatisticas.erros DESC, estatisticas.facilidade
                    Sort Method: quicksort  Memory: 81kB
                    Buffers: shared hit=5282
                    ->  Subquery Scan on estatisticas (actual time=1.213..1.284 rows=480 loops=1)
                          Buffers: shared hit=5282
                          ->  HashAggregate (actual time=1.213..1.261 rows=480 loops=1)
                                Group Key: d.nome, f.id, r.facilidade
                                Batches: 1  Memory Usage: 241kB
                                Buffers: shared hit=5282
                                ->  Nested Loop (actual time=0.011..0.946 rows=1889 loops=1)
                                      Buffers: shared hit=5282
                                      ->  Nested Loop (actual time=0.009..0.348 rows=480 loops=1)
                                            Buffers: shared hit=1971
                                            ->  Nested Loop (actual time=0.007..0.078 rows=480 loops=1)
                                                  Buffers: shared hit=51
                                                  ->  Nested Loop (actual time=0.004..0.007 rows=4 loops=1)
                                                        Buffers: shared hit=7
                                                        ->  Seq Scan on usuarios u (actual time=0.003..0.005 rows=1 loops=1)
                                                              Filter: (id = 1164)
                                                              Rows Removed by Filter: 201
                                                              Buffers: shared hit=4
                                                        ->  Bitmap Heap Scan on disciplinas d (actual time=0.001..0.001 rows=4 loops=1)
                                                              Recheck Cond: (usuario_id = 1164)
                                                              Heap Blocks: exact=1
                                                              Buffers: shared hit=3
                                                              ->  Bitmap Index Scan on uq_disciplinas_usuario_nome (actual time=0.001..0.001 rows=4 loops=1)
                                                                    Index Cond: (usuario_id = 1164)
                                                                    Buffers: shared hit=2
                                                  ->  Index Scan using ix_flashcards_disciplina_id on flashcards f (actual time=0.001..0.012 rows=120 loops=4)
                                                        Index Cond: (disciplina_id = d.id)
                                                        Buffers: shared hit=44
                                            ->  Index Scan using pk_revisoes on revisoes r (actual time=0.000..0.000 rows=1 loops=480)
                                                  Index Cond: (flashcard_id = f.id)
                                                  Buffers: shared hit=1920
                                      ->  Index Scan using ix_historico_revisoes_flashcard_revisado_em on historico_revisoes h (actual time=0.000..0.001 rows=4 loops=480)
                                            Index Cond: (flashcard_id = f.id)
                                            Buffers: shared hit=3311
Planning:
  Buffers: shared hit=65
Planning Time: 0.216 ms
Execution Time: 1.465 ms
```
</details>

<details><summary>4. sequência — depois: + INCLUDE (nota)</summary>

```
Result (actual time=1.170..1.172 rows=1 loops=1)
  Buffers: shared hit=1503
  CTE hoje
    ->  Seq Scan on usuarios (actual time=0.003..0.006 rows=1 loops=1)
          Filter: (id = 1164)
          Rows Removed by Filter: 201
          Buffers: shared hit=4
  CTE dias_estudados
    ->  HashAggregate (actual time=1.135..1.138 rows=29 loops=1)
          Group Key: "*SELECT* 1".dia
          Batches: 1  Memory Usage: 40kB
          Buffers: shared hit=1499
          ->  Subquery Scan on "*SELECT* 1" (actual time=0.011..1.004 rows=1889 loops=1)
                Buffers: shared hit=1499
                ->  Nested Loop (actual time=0.010..0.904 rows=1889 loops=1)
                      Buffers: shared hit=1499
                      ->  Nested Loop (actual time=0.008..0.082 rows=480 loops=1)
                            Buffers: shared hit=51
                            ->  Nested Loop (actual time=0.005..0.009 rows=4 loops=1)
                                  Buffers: shared hit=7
                                  ->  Seq Scan on usuarios u (actual time=0.003..0.006 rows=1 loops=1)
                                        Filter: (id = 1164)
                                        Rows Removed by Filter: 201
                                        Buffers: shared hit=4
                                  ->  Bitmap Heap Scan on disciplinas d (actual time=0.001..0.002 rows=4 loops=1)
                                        Recheck Cond: (usuario_id = 1164)
                                        Heap Blocks: exact=1
                                        Buffers: shared hit=3
                                        ->  Bitmap Index Scan on uq_disciplinas_usuario_nome (actual time=0.001..0.001 rows=4 loops=1)
                                              Index Cond: (usuario_id = 1164)
                                              Buffers: shared hit=2
                            ->  Index Scan using ix_flashcards_disciplina_id on flashcards f (actual time=0.001..0.012 rows=120 loops=4)
                                  Index Cond: (disciplina_id = d.id)
                                  Buffers: shared hit=44
                      ->  Index Only Scan using ix_historico_revisoes_flashcard_revisado_em on historico_revisoes h (actual time=0.000..0.001 rows=4 loops=480)
                            Index Cond: (flashcard_id = f.id)
                            Heap Fetches: 0
                            Buffers: shared hit=1448
  CTE sequencias
    ->  HashAggregate (actual time=1.151..1.152 rows=8 loops=1)
          Group Key: (dias_estudados.dia - (row_number() OVER (?))::integer)
          Batches: 1  Memory Usage: 40kB
          Buffers: shared hit=1499
          ->  WindowAgg (actual time=1.143..1.147 rows=29 loops=1)
                Buffers: shared hit=1499
                ->  Sort (actual time=1.142..1.143 rows=29 loops=1)
                      Sort Key: dias_estudados.dia
                      Sort Method: quicksort  Memory: 25kB
                      Buffers: shared hit=1499
                      ->  CTE Scan on dias_estudados (actual time=1.135..1.140 rows=29 loops=1)
                            Buffers: shared hit=1499
  CTE atual
    ->  Nested Loop (actual time=1.154..1.154 rows=0 loops=1)
          Join Filter: (s.fim >= (h_1.dia - 1))
          Rows Removed by Join Filter: 8
          Buffers: shared hit=1499
          ->  CTE Scan on hoje h_1 (actual time=0.000..0.000 rows=1 loops=1)
          ->  CTE Scan on sequencias s (actual time=1.151..1.153 rows=8 loops=1)
                Buffers: shared hit=1499
  CTE maior
    ->  Limit (actual time=0.002..0.002 rows=1 loops=1)
          ->  Sort (actual time=0.002..0.002 rows=1 loops=1)
                Sort Key: sequencias.dias DESC, sequencias.fim DESC
                Sort Method: top-N heapsort  Memory: 25kB
                ->  CTE Scan on sequencias (actual time=0.000..0.001 rows=8 loops=1)
  InitPlan 6 (returns $7)
    ->  CTE Scan on hoje (actual time=0.004..0.006 rows=1 loops=1)
          Buffers: shared hit=4
  InitPlan 7 (returns $8)
    ->  CTE Scan on atual (actual time=1.154..1.154 rows=0 loops=1)
          Buffers: shared hit=1499
  InitPlan 8 (returns $9)
    ->  CTE Scan on atual atual_1 (actual time=0.000..0.000 rows=0 loops=1)
  InitPlan 9 (returns $10)
    ->  CTE Scan on atual atual_2 (actual time=0.000..0.000 rows=0 loops=1)
  InitPlan 10 (returns $11)
    ->  Hash Join (actual time=0.003..0.004 rows=0 loops=1)
          Hash Cond: (dias_estudados_1.dia = hoje_1.dia)
          ->  CTE Scan on dias_estudados dias_estudados_1 (actual time=0.000..0.001 rows=29 loops=1)
          ->  Hash (actual time=0.000..0.000 rows=1 loops=1)
                Buckets: 1024  Batches: 1  Memory Usage: 9kB
                ->  CTE Scan on hoje hoje_1 (actual time=0.000..0.000 rows=1 loops=1)
  InitPlan 11 (returns $12)
    ->  CTE Scan on maior (actual time=0.002..0.002 rows=1 loops=1)
  InitPlan 12 (returns $13)
    ->  CTE Scan on maior maior_1 (actual time=0.000..0.000 rows=1 loops=1)
  InitPlan 13 (returns $14)
    ->  CTE Scan on maior maior_2 (actual time=0.000..0.000 rows=1 loops=1)
  InitPlan 14 (returns $15)
    ->  Aggregate (actual time=0.002..0.003 rows=1 loops=1)
          ->  CTE Scan on dias_estudados dias_estudados_2 (actual time=0.000..0.001 rows=29 loops=1)
Planning:
  Buffers: shared hit=58
Planning Time: 0.297 ms
Execution Time: 1.187 ms
```
</details>

<details><summary>4. cards difíceis — depois: + INCLUDE (nota)</summary>

```
Sort (actual time=1.215..1.217 rows=54 loops=1)
  Sort Key: ranqueado.disciplina, ranqueado.posicao, ranqueado.flashcard_id
  Sort Method: quicksort  Memory: 32kB
  Buffers: shared hit=3419
  ->  Subquery Scan on ranqueado (actual time=1.144..1.195 rows=54 loops=1)
        Buffers: shared hit=3419
        ->  WindowAgg (actual time=1.144..1.186 rows=54 loops=1)
              Run Condition: (dense_rank() OVER (?) <= 5)
              Buffers: shared hit=3419
              ->  Sort (actual time=1.143..1.155 rows=480 loops=1)
                    Sort Key: estatisticas.disciplina_id, estatisticas.erros DESC, estatisticas.facilidade
                    Sort Method: quicksort  Memory: 81kB
                    Buffers: shared hit=3419
                    ->  Subquery Scan on estatisticas (actual time=0.976..1.047 rows=480 loops=1)
                          Buffers: shared hit=3419
                          ->  HashAggregate (actual time=0.975..1.023 rows=480 loops=1)
                                Group Key: d.nome, f.id, r.facilidade
                                Batches: 1  Memory Usage: 241kB
                                Buffers: shared hit=3419
                                ->  Nested Loop (actual time=0.008..0.717 rows=1889 loops=1)
                                      Buffers: shared hit=3419
                                      ->  Nested Loop (actual time=0.007..0.323 rows=480 loops=1)
                                            Buffers: shared hit=1971
                                            ->  Nested Loop (actual time=0.005..0.074 rows=480 loops=1)
                                                  Buffers: shared hit=51
                                                  ->  Nested Loop (actual time=0.004..0.007 rows=4 loops=1)
                                                        Buffers: shared hit=7
                                                        ->  Seq Scan on usuarios u (actual time=0.003..0.005 rows=1 loops=1)
                                                              Filter: (id = 1164)
                                                              Rows Removed by Filter: 201
                                                              Buffers: shared hit=4
                                                        ->  Bitmap Heap Scan on disciplinas d (actual time=0.001..0.001 rows=4 loops=1)
                                                              Recheck Cond: (usuario_id = 1164)
                                                              Heap Blocks: exact=1
                                                              Buffers: shared hit=3
                                                              ->  Bitmap Index Scan on uq_disciplinas_usuario_nome (actual time=0.001..0.001 rows=4 loops=1)
                                                                    Index Cond: (usuario_id = 1164)
                                                                    Buffers: shared hit=2
                                                  ->  Index Scan using ix_flashcards_disciplina_id on flashcards f (actual time=0.001..0.011 rows=120 loops=4)
                                                        Index Cond: (disciplina_id = d.id)
                                                        Buffers: shared hit=44
                                            ->  Index Scan using pk_revisoes on revisoes r (actual time=0.000..0.000 rows=1 loops=480)
                                                  Index Cond: (flashcard_id = f.id)
                                                  Buffers: shared hit=1920
                                      ->  Index Only Scan using ix_historico_revisoes_flashcard_revisado_em on historico_revisoes h (actual time=0.000..0.001 rows=4 loops=480)
                                            Index Cond: (flashcard_id = f.id)
                                            Heap Fetches: 0
                                            Buffers: shared hit=1448
Planning:
  Buffers: shared hit=65
Planning Time: 0.206 ms
Execution Time: 1.229 ms
```
</details>

