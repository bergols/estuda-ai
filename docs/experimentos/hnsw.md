# Experimento: busca vetorial com e sem HNSW

Gerado por `backend/scripts/experimento_hnsw.py` (não edite à mão; rode o script de novo).

- 50,012 trechos com `vector(384)`, pgvector 0.8.7, Postgres no Docker (Colima, 2 CPUs, 4 GB).
- Tabela `trechos`: 211 MB; índice HNSW (m=16, ef_construction=64): 102 MB.
- 50 consultas por cenário, k = 10. Tempo = `Execution Time` do EXPLAIN ANALYZE (não inclui rede nem o cálculo do embedding).
- Recall@10 = fração dos 10 vizinhos exatos que a busca devolveu.
- Cache aquecido: cada cenário roda as consultas uma vez antes de medir.

## Cenário A — disciplina grande: "Grande" (40,000 trechos, 80.0% da tabela)

| Estratégia | Mediana (ms) | p95 (ms) | Recall@10 | Linhas devolvidas | Plano |
|---|---:|---:|---:|---:|---|
| Exata, sem HNSW (DROP INDEX na transação) | 19.70 | 23.02 | 1.000 | 10.0 de 10 | `Limit → Sort → Index Scan (ix_trechos_disciplina_id)` |
| HNSW, ef_search = 40 | 0.50 | 0.68 | 0.862 | 10.0 de 10 | `Limit → Index Scan (ix_trechos_embedding_hnsw)` |
| HNSW, ef_search = 100 | 0.65 | 0.83 | 0.922 | 10.0 de 10 | `Limit → Index Scan (ix_trechos_embedding_hnsw)` |
| HNSW, ef_search = 200 | 0.77 | 1.30 | 0.998 | 10.0 de 10 | `Limit → Index Scan (ix_trechos_embedding_hnsw)` |

<details><summary>EXPLAIN ANALYZE — Exata, sem HNSW (DROP INDEX na transação)</summary>

```
Limit (actual time=21.224..21.225 rows=10 loops=1)
  Buffers: shared hit=28469
  ->  Sort (actual time=21.223..21.224 rows=10 loops=1)
        Sort Key: ((embedding <=> '[...384 números...]'::vector))
        Sort Method: top-N heapsort  Memory: 25kB
        Buffers: shared hit=28469
        ->  Index Scan using ix_trechos_disciplina_id on trechos (actual time=0.011..19.314 rows=40000 loops=1)
              Index Cond: (disciplina_id = 27)
              Buffers: shared hit=28469
Planning Time: 0.039 ms
Execution Time: 21.235 ms
```
</details>

<details><summary>EXPLAIN ANALYZE — HNSW, ef_search = 40</summary>

```
Limit (actual time=0.184..0.188 rows=10 loops=1)
  Buffers: shared hit=772
  ->  Index Scan using ix_trechos_embedding_hnsw on trechos (actual time=0.184..0.188 rows=10 loops=1)
        Order By: (embedding <=> '[...384 números...]'::vector)
        Filter: (disciplina_id = 27)
        Rows Removed by Filter: 6
        Buffers: shared hit=772
Planning:
  Buffers: shared hit=1
Planning Time: 0.011 ms
Execution Time: 0.191 ms
```
</details>

<details><summary>EXPLAIN ANALYZE — HNSW, ef_search = 100</summary>

```
Limit (actual time=0.333..0.339 rows=10 loops=1)
  Buffers: shared hit=1107
  ->  Index Scan using ix_trechos_embedding_hnsw on trechos (actual time=0.333..0.338 rows=10 loops=1)
        Order By: (embedding <=> '[...384 números...]'::vector)
        Filter: (disciplina_id = 27)
        Rows Removed by Filter: 6
        Buffers: shared hit=1107
Planning:
  Buffers: shared hit=1
Planning Time: 0.013 ms
Execution Time: 0.342 ms
```
</details>

<details><summary>EXPLAIN ANALYZE — HNSW, ef_search = 200</summary>

```
Limit (actual time=0.404..0.409 rows=10 loops=1)
  Buffers: shared hit=1356
  ->  Index Scan using ix_trechos_embedding_hnsw on trechos (actual time=0.404..0.408 rows=10 loops=1)
        Order By: (embedding <=> '[...384 números...]'::vector)
        Filter: (disciplina_id = 27)
        Rows Removed by Filter: 6
        Buffers: shared hit=1356
Planning:
  Buffers: shared hit=1
Planning Time: 0.011 ms
Execution Time: 0.412 ms
```
</details>

## Cenário B — disciplina pequena: "Pequena 06" (500 trechos, 1.0% da tabela)

| Estratégia | Mediana (ms) | p95 (ms) | Recall@10 | Linhas devolvidas | Plano |
|---|---:|---:|---:|---:|---|
| Exata, sem HNSW (DROP INDEX na transação) | 0.19 | 0.22 | 1.000 | 10.0 de 10 | `Limit → Sort → Index Scan (ix_trechos_disciplina_id)` |
| Planejador decide (todos os índices presentes) | 0.20 | 0.21 | 1.000 | 10.0 de 10 | `Limit → Sort → Index Scan (ix_trechos_disciplina_id)` |
| HNSW forçado, iterative_scan = off | 0.44 | 0.50 | 0.148 | 1.5 de 10 | `Limit → Index Scan (ix_trechos_embedding_hnsw)` |
| HNSW forçado, iterative_scan = strict_order | 3.32 | 13.41 | 0.878 | 10.0 de 10 | `Limit → Index Scan (ix_trechos_embedding_hnsw)` |

<details><summary>EXPLAIN ANALYZE — Exata, sem HNSW (DROP INDEX na transação)</summary>

```
Limit (actual time=0.195..0.196 rows=10 loops=1)
  Buffers: shared hit=343
  ->  Sort (actual time=0.195..0.196 rows=10 loops=1)
        Sort Key: ((embedding <=> '[...384 números...]'::vector))
        Sort Method: top-N heapsort  Memory: 25kB
        Buffers: shared hit=343
        ->  Index Scan using ix_trechos_disciplina_id on trechos (actual time=0.002..0.161 rows=500 loops=1)
              Index Cond: (disciplina_id = 33)
              Buffers: shared hit=343
Planning Time: 0.009 ms
Execution Time: 0.199 ms
```
</details>

<details><summary>EXPLAIN ANALYZE — Planejador decide (todos os índices presentes)</summary>

```
Limit (actual time=0.575..0.576 rows=10 loops=1)
  Buffers: shared hit=343
  ->  Sort (actual time=0.575..0.575 rows=10 loops=1)
        Sort Key: ((embedding <=> '[...384 números...]'::vector))
        Sort Method: top-N heapsort  Memory: 25kB
        Buffers: shared hit=343
        ->  Index Scan using ix_trechos_disciplina_id on trechos (actual time=0.016..0.518 rows=500 loops=1)
              Index Cond: (disciplina_id = 33)
              Buffers: shared hit=343
Planning:
  Buffers: shared hit=1
Planning Time: 0.087 ms
Execution Time: 0.590 ms
```
</details>

<details><summary>EXPLAIN ANALYZE — HNSW forçado, iterative_scan = off</summary>

```
Limit (actual time=0.207..0.207 rows=0 loops=1)
  Buffers: shared hit=837
  ->  Index Scan using ix_trechos_embedding_hnsw on trechos (actual time=0.207..0.207 rows=0 loops=1)
        Order By: (embedding <=> '[...384 números...]'::vector)
        Filter: (disciplina_id = 33)
        Rows Removed by Filter: 40
        Buffers: shared hit=837
Planning:
  Buffers: shared hit=1
Planning Time: 0.009 ms
Execution Time: 0.209 ms
```
</details>

<details><summary>EXPLAIN ANALYZE — HNSW forçado, iterative_scan = strict_order</summary>

```
Limit (actual time=0.849..2.097 rows=10 loops=1)
  Buffers: shared hit=3035
  ->  Index Scan using ix_trechos_embedding_hnsw on trechos (actual time=0.849..2.096 rows=10 loops=1)
        Order By: (embedding <=> '[...384 números...]'::vector)
        Filter: (disciplina_id = 33)
        Rows Removed by Filter: 806
        Buffers: shared hit=3035
Planning:
  Buffers: shared hit=1
Planning Time: 0.019 ms
Execution Time: 2.104 ms
```
</details>

## Carga: o custo de manter o índice

INSERT de 2.000 trechos linha a linha (executemany), desfeito com ROLLBACK:

| Situação | Linhas por segundo |
|---|---:|
| Com o índice HNSW | 1,398 |
| Sem o índice HNSW | 22,537 |

