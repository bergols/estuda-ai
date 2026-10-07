# Experimento: índice da fila do dia

Gerado por `backend/scripts/experimento_fila.py` (não edite à mão; rode o script de novo).

- Dois cenários com o MESMO total de 200 mil cards em `revisoes`, divididos entre poucos ou muitos usuários (4 disciplinas cada). `proxima_revisao` espalhada entre 30 dias atrás e 60 à frente (~1/3 vencidos). Tudo criado e desfeito numa transação (ROLLBACK).
- 30 usuários sorteados por medição, `LIMIT 20`, cache aquecido. Tempo = `Execution Time` do EXPLAIN ANALYZE, em milissegundos (mediana; p95 entre parênteses).
- Consultas: "Usuário (JOIN)" foi a 1a versão; "Usuário (LATERAL)" e "1 disciplina" são as da API (`app/servicos/revisao.py`).

## Cenário A: 50 usuários x 1.000 cards por disciplina

200,480 linhas em `revisoes`, 66,619 vencidas; cada usuário tem 4,000 cards (2.0% do total).

| Índice | Usuário (JOIN) | Usuário (LATERAL, a da API) | 1 disciplina |
|---|---:|---:|---:|
| Sem índice de fila (só a PK) | 41.78 (49.12) | 58.18 (70.82) | 37.78 (46.41) |
| Simples: (proxima_revisao) | 0.90 (1.48) | 3.71 (4.94) | 0.96 (1.57) |
| Simples: (disciplina_id) | 3.00 (5.18) | 1.02 (1.47) | 0.74 (0.78) |
| Composto: (disciplina_id, proxima_revisao) — o escolhido | 2.42 (3.32) | 0.24 (0.27) | 0.07 (0.08) |
| Composto invertido: (proxima_revisao, disciplina_id) | 0.86 (1.44) | 0.46 (0.65) | 0.15 (0.17) |

<details><summary>Caminho escolhido pelo planejador em cada caso</summary>

| Índice | Consulta | Plano | Buffers |
|---|---|---|---:|
| Sem índice de fila (só a PK) | Usuário (JOIN) | `Limit → Nested Loop → Gather Merge → Sort → Hash Join → Seq Scan → Hash → Seq Scan → Index Scan (pk_flashcards)` | 4724 |
| Sem índice de fila (só a PK) | Usuário (LATERAL, a da API) | `Limit → Sort → Nested Loop → Nested Loop → Seq Scan → Limit → Sort → Seq Scan → Index Scan (uq_flashcards_id_disciplina_id)` | 18527 |
| Sem índice de fila (só a PK) | 1 disciplina | `Limit → Nested Loop → Nested Loop → Gather Merge → Sort → Seq Scan → Index Scan (pk_flashcards) → Materialize → Seq Scan` | 4724 |
| Simples: (proxima_revisao) | Usuário (JOIN) | `Limit → Incremental Sort → Nested Loop → Nested Loop → Index Scan (exp_proxima) → Materialize → Seq Scan → Index Scan (pk_flashcards)` | 1392 |
| Simples: (proxima_revisao) | Usuário (LATERAL, a da API) | `Limit → Sort → Nested Loop → Nested Loop → Seq Scan → Limit → Incremental Sort → Index Scan (exp_proxima) → Index Scan (uq_flashcards_id_disciplina_id)` | 16522 |
| Simples: (proxima_revisao) | 1 disciplina | `Limit → Incremental Sort → Nested Loop → Nested Loop → Index Scan (exp_proxima) → Index Scan (pk_flashcards) → Materialize → Seq Scan` | 5928 |
| Simples: (disciplina_id) | Usuário (JOIN) | `Limit → Sort → Nested Loop → Nested Loop → Seq Scan → Index Scan (exp_disciplina) → Index Scan (pk_flashcards)` | 5794 |
| Simples: (disciplina_id) | Usuário (LATERAL, a da API) | `Limit → Sort → Nested Loop → Nested Loop → Seq Scan → Limit → Sort → Index Scan (exp_disciplina) → Index Scan (uq_flashcards_id_disciplina_id)` | 710 |
| Simples: (disciplina_id) | 1 disciplina | `Limit → Sort → Nested Loop → Seq Scan → Nested Loop → Index Scan (exp_disciplina) → Index Scan (pk_flashcards)` | 1500 |
| Composto: (disciplina_id, proxima_revisao) — o escolhido | Usuário (JOIN) | `Limit → Sort → Nested Loop → Nested Loop → Seq Scan → Index Scan (ix_revisoes_disciplina_proxima_revisao) → Index Scan (pk_flashcards)` | 6940 |
| Composto: (disciplina_id, proxima_revisao) — o escolhido | Usuário (LATERAL, a da API) | `Limit → Sort → Nested Loop → Nested Loop → Seq Scan → Limit → Incremental Sort → Index Scan (ix_revisoes_disciplina_proxima_revisao) → Index Scan (uq_flashcards_id_disciplina_id)` | 419 |
| Composto: (disciplina_id, proxima_revisao) — o escolhido | 1 disciplina | `Limit → Incremental Sort → Nested Loop → Nested Loop → Index Scan (ix_revisoes_disciplina_proxima_revisao) → Index Scan (pk_flashcards) → Materialize → Seq Scan` | 111 |
| Composto invertido: (proxima_revisao, disciplina_id) | Usuário (JOIN) | `Limit → Incremental Sort → Nested Loop → Nested Loop → Index Scan (exp_invertido) → Materialize → Seq Scan → Index Scan (pk_flashcards)` | 1393 |
| Composto invertido: (proxima_revisao, disciplina_id) | Usuário (LATERAL, a da API) | `Limit → Sort → Nested Loop → Nested Loop → Seq Scan → Limit → Incremental Sort → Index Scan (exp_invertido) → Index Scan (uq_flashcards_id_disciplina_id)` | 479 |
| Composto invertido: (proxima_revisao, disciplina_id) | 1 disciplina | `Limit → Incremental Sort → Nested Loop → Nested Loop → Index Scan (exp_invertido) → Index Scan (pk_flashcards) → Materialize → Seq Scan` | 133 |

</details>

## Cenário B: 500 usuários x 100 cards por disciplina

200,480 linhas em `revisoes`, 66,513 vencidas; cada usuário tem 400 cards (0.2% do total).

| Índice | Usuário (JOIN) | Usuário (LATERAL, a da API) | 1 disciplina |
|---|---:|---:|---:|
| Sem índice de fila (só a PK) | 82.52 (96.95) | 75.21 (85.20) | 78.22 (91.77) |
| Simples: (proxima_revisao) | 8.23 (12.13) | 60.93 (64.46) | 77.86 (83.97) |
| Simples: (disciplina_id) | 0.26 (0.29) | 0.26 (0.29) | 0.08 (0.10) |
| Composto: (disciplina_id, proxima_revisao) — o escolhido | 0.25 (0.32) | 0.20 (0.23) | 0.06 (0.08) |
| Composto invertido: (proxima_revisao, disciplina_id) | 3.82 (4.60) | 2.13 (2.75) | 0.52 (0.71) |

<details><summary>Caminho escolhido pelo planejador em cada caso</summary>

| Índice | Consulta | Plano | Buffers |
|---|---|---|---:|
| Sem índice de fila (só a PK) | Usuário (JOIN) | `Limit → Gather Merge → Sort → Nested Loop → Hash Join → Seq Scan → Hash → Bitmap Heap Scan → Bitmap Index Scan (uq_disciplinas_usuario_nome) → Index Scan (pk_flashcards)` | 9652 |
| Sem índice de fila (só a PK) | Usuário (LATERAL, a da API) | `Limit → Sort → Nested Loop → Nested Loop → Bitmap Heap Scan → Bitmap Index Scan (uq_disciplinas_usuario_nome) → Limit → Sort → Seq Scan → Index Scan (uq_flashcards_id_disciplina_id)` | 36711 |
| Sem índice de fila (só a PK) | 1 disciplina | `Limit → Nested Loop → Nested Loop → Gather Merge → Sort → Seq Scan → Materialize → Index Scan (uq_disciplinas_id_usuario_id) → Index Scan (uq_flashcards_id_disciplina_id)` | 9270 |
| Simples: (proxima_revisao) | Usuário (JOIN) | `Limit → Incremental Sort → Nested Loop → Nested Loop → Index Scan (exp_proxima) → Memoize → Index Scan (pk_disciplinas) → Index Scan (pk_flashcards)` | 13930 |
| Simples: (proxima_revisao) | Usuário (LATERAL, a da API) | `Limit → Sort → Nested Loop → Nested Loop → Bitmap Heap Scan → Bitmap Index Scan (uq_disciplinas_usuario_nome) → Limit → Sort → Bitmap Heap Scan → Bitmap Index Scan (exp_proxima) → Index Scan (uq_flashcards_id_disciplina_id)` | 19951 |
| Simples: (proxima_revisao) | 1 disciplina | `Limit → Nested Loop → Nested Loop → Gather Merge → Sort → Seq Scan → Materialize → Index Scan (uq_disciplinas_id_usuario_id) → Index Scan (uq_flashcards_id_disciplina_id)` | 9270 |
| Simples: (disciplina_id) | Usuário (JOIN) | `Limit → Sort → Nested Loop → Nested Loop → Bitmap Heap Scan → Bitmap Index Scan (uq_disciplinas_usuario_nome) → Index Scan (exp_disciplina) → Index Scan (pk_flashcards)` | 576 |
| Simples: (disciplina_id) | Usuário (LATERAL, a da API) | `Limit → Sort → Nested Loop → Nested Loop → Bitmap Heap Scan → Bitmap Index Scan (uq_disciplinas_usuario_nome) → Limit → Sort → Index Scan (exp_disciplina) → Memoize → Index Scan (uq_flashcards_id_disciplina_id)` | 376 |
| Simples: (disciplina_id) | 1 disciplina | `Limit → Sort → Nested Loop → Nested Loop → Index Scan (uq_disciplinas_id_usuario_id) → Index Scan (exp_disciplina) → Index Scan (uq_flashcards_id_disciplina_id)` | 160 |
| Composto: (disciplina_id, proxima_revisao) — o escolhido | Usuário (JOIN) | `Limit → Sort → Nested Loop → Nested Loop → Bitmap Heap Scan → Bitmap Index Scan (uq_disciplinas_usuario_nome) → Index Scan (ix_revisoes_disciplina_proxima_revisao) → Index Scan (pk_flashcards)` | 666 |
| Composto: (disciplina_id, proxima_revisao) — o escolhido | Usuário (LATERAL, a da API) | `Limit → Sort → Nested Loop → Nested Loop → Bitmap Heap Scan → Bitmap Index Scan (uq_disciplinas_usuario_nome) → Limit → Incremental Sort → Index Scan (ix_revisoes_disciplina_proxima_revisao) → Memoize → Index Scan (uq_flashcards_id_disciplina_id)` | 407 |
| Composto: (disciplina_id, proxima_revisao) — o escolhido | 1 disciplina | `Limit → Incremental Sort → Nested Loop → Nested Loop → Index Scan (ix_revisoes_disciplina_proxima_revisao) → Materialize → Index Scan (uq_disciplinas_id_usuario_id) → Index Scan (uq_flashcards_id_disciplina_id)` | 108 |
| Composto invertido: (proxima_revisao, disciplina_id) | Usuário (JOIN) | `Limit → Sort → Nested Loop → Nested Loop → Seq Scan → Index Scan (exp_invertido) → Index Scan (pk_flashcards)` | 2439 |
| Composto invertido: (proxima_revisao, disciplina_id) | Usuário (LATERAL, a da API) | `Limit → Sort → Nested Loop → Nested Loop → Index Scan (uq_disciplinas_usuario_nome) → Limit → Incremental Sort → Index Scan (exp_invertido) → Memoize → Index Scan (uq_flashcards_id_disciplina_id)` | 969 |
| Composto invertido: (proxima_revisao, disciplina_id) | 1 disciplina | `Limit → Incremental Sort → Nested Loop → Nested Loop → Index Scan (exp_invertido) → Materialize → Index Scan (uq_disciplinas_id_usuario_id) → Index Scan (uq_flashcards_id_disciplina_id)` | 212 |

</details>

## E o índice parcial?

O índice "perfeito" para a fila conteria só as linhas vencidas:

```sql
CREATE INDEX ... ON revisoes (disciplina_id, proxima_revisao)
WHERE proxima_revisao <= now();
```

O Postgres recusa: `functions in index predicate must be marked IMMUTABLE`

O predicado de um índice parcial é avaliado quando a linha é gravada, e o resultado precisa ser sempre o mesmo. `now()` muda a cada instante: um card que não estava vencido ao ser gravado venceria depois sem nunca entrar no índice. Ver `docs/repeticao-espacada.md`.

<details><summary>EXPLAIN ANALYZE — índice escolhido, cenário A — Usuário (JOIN)</summary>

```
Limit (actual time=2.390..2.392 rows=20 loops=1)
  Buffers: shared hit=6940
  ->  Sort (actual time=2.389..2.390 rows=20 loops=1)
        Sort Key: r.proxima_revisao, r.flashcard_id
        Sort Method: top-N heapsort  Memory: 27kB
        Buffers: shared hit=6940
        ->  Nested Loop (actual time=0.018..2.181 rows=1351 loops=1)
              Buffers: shared hit=6940
              ->  Nested Loop (actual time=0.013..0.693 rows=1351 loops=1)
                    Buffers: shared hit=1536
                    ->  Seq Scan on disciplinas d (actual time=0.007..0.010 rows=4 loops=1)
                          Filter: (usuario_id = 132)
                          Rows Removed by Filter: 222
                          Buffers: shared hit=3
                    ->  Index Scan using ix_revisoes_disciplina_proxima_revisao on revisoes r (actual time=0.004..0.137 rows=338 loops=4)
                          Index Cond: ((disciplina_id = d.id) AND (proxima_revisao < '...'::timestamp with time zone))
                          Buffers: shared hit=1533
              ->  Index Scan using pk_flashcards on flashcards f (actual time=0.001..0.001 rows=1 loops=1351)
                    Index Cond: (id = r.flashcard_id)
                    Buffers: shared hit=5404
Planning:
  Buffers: shared hit=21
Planning Time: 0.130 ms
Execution Time: 2.404 ms
```
</details>

<details><summary>EXPLAIN ANALYZE — índice escolhido, cenário A — Usuário (LATERAL, a da API)</summary>

```
Limit (actual time=0.253..0.255 rows=20 loops=1)
  Buffers: shared hit=419
  ->  Sort (actual time=0.253..0.254 rows=20 loops=1)
        Sort Key: r2.proxima_revisao, r2.flashcard_id
        Sort Method: top-N heapsort  Memory: 29kB
        Buffers: shared hit=419
        ->  Nested Loop (actual time=0.040..0.237 rows=80 loops=1)
              Buffers: shared hit=419
              ->  Nested Loop (actual time=0.035..0.104 rows=80 loops=1)
                    Buffers: shared hit=99
                    ->  Seq Scan on disciplinas d (actual time=0.006..0.010 rows=4 loops=1)
                          Filter: (usuario_id = 132)
                          Rows Removed by Filter: 222
                          Buffers: shared hit=3
                    ->  Limit (actual time=0.020..0.022 rows=20 loops=4)
                          Buffers: shared hit=96
                          ->  Incremental Sort (actual time=0.019..0.020 rows=20 loops=4)
                                Sort Key: r2.proxima_revisao, r2.flashcard_id
                                Presorted Key: r2.proxima_revisao
                                Full-sort Groups: 4  Sort Method: quicksort  Average Memory: 27kB  Peak Memory: 27kB
                                Buffers: shared hit=96
                                ->  Index Scan using ix_revisoes_disciplina_proxima_revisao on revisoes r2 (actual time=0.005..0.016 rows=21 loops=4)
                                      Index Cond: ((disciplina_id = d.id) AND (proxima_revisao < '...'::timestamp with time zone))
                                      Buffers: shared hit=96
              ->  Index Scan using uq_flashcards_id_disciplina_id on flashcards f (actual time=0.001..0.001 rows=1 loops=80)
                    Index Cond: (id = r2.flashcard_id)
                    Buffers: shared hit=320
Planning:
  Buffers: shared hit=11
Planning Time: 0.081 ms
Execution Time: 0.268 ms
```
</details>

<details><summary>EXPLAIN ANALYZE — índice escolhido, cenário A — 1 disciplina</summary>

```
Limit (actual time=0.067..0.069 rows=20 loops=1)
  Buffers: shared hit=111
  ->  Incremental Sort (actual time=0.067..0.068 rows=20 loops=1)
        Sort Key: r.proxima_revisao, r.flashcard_id
        Presorted Key: r.proxima_revisao
        Full-sort Groups: 1  Sort Method: quicksort  Average Memory: 27kB  Peak Memory: 27kB
        Buffers: shared hit=111
        ->  Nested Loop (actual time=0.015..0.063 rows=21 loops=1)
              Buffers: shared hit=111
              ->  Nested Loop (actual time=0.008..0.045 rows=21 loops=1)
                    Buffers: shared hit=108
                    ->  Index Scan using ix_revisoes_disciplina_proxima_revisao on revisoes r (actual time=0.005..0.013 rows=21 loops=1)
                          Index Cond: ((disciplina_id = 533) AND (proxima_revisao < '...'::timestamp with time zone))
                          Buffers: shared hit=24
                    ->  Index Scan using pk_flashcards on flashcards f (actual time=0.001..0.001 rows=1 loops=21)
                          Index Cond: (id = r.flashcard_id)
                          Buffers: shared hit=84
              ->  Materialize (actual time=0.000..0.000 rows=1 loops=21)
                    Buffers: shared hit=3
                    ->  Seq Scan on disciplinas d (actual time=0.006..0.009 rows=1 loops=1)
                          Filter: ((id = 533) AND (usuario_id = 132))
                          Rows Removed by Filter: 225
                          Buffers: shared hit=3
Planning:
  Buffers: shared hit=21
Planning Time: 0.072 ms
Execution Time: 0.076 ms
```
</details>

