"""Popula o banco com trechos sintéticos para o experimento do HNSW.

    docker compose exec backend python -m scripts.seed_experimento [--trechos 50000]

Cria o usuário experimento@estuda-ai.local (apagando o anterior e, em cascata,
tudo dele), com:
  - 1 disciplina "Grande", com 80% dos trechos;
  - 20 disciplinas "Pequena NN", que dividem os 20% restantes.

Vetores: sintéticos, mas AGRUPADOS. Sorteamos 50 "tópicos" (centros) e cada
trecho é um centro + ruído, normalizado. Isso imita embeddings reais (textos do
mesmo assunto ficam próximos). Vetores uniformemente aleatórios seriam o pior
caso para qualquer índice aproximado e dariam números pessimistas demais.

Técnicas de carga em massa usadas aqui (e por quê):
  1. COPY em vez de INSERT: um fluxo único de linhas, sem parse/plano por linha.
  2. O índice HNSW é apagado ANTES da carga e recriado DEPOIS: construir o grafo
     uma vez, com todos os dados, é muito mais rápido que inseri-los um a um.
  3. maintenance_work_mem maior durante o CREATE INDEX: o grafo cabe na memória.
  4. ANALYZE no fim: atualiza as estatísticas que o planejador usa para escolher
     entre os índices (sem isso ele estima errado o tamanho de cada disciplina).
"""

import argparse
import hashlib
import time

import numpy as np
import psycopg

from app.config import get_settings
from app.models import EMBEDDING_DIM

EMAIL = "experimento@estuda-ai.local"
TOPICOS = 50
TRECHOS_POR_MATERIAL = 1000

VOCABULARIO = (
    "índice árvore chave tabela linha coluna consulta junção transação bloqueio "
    "isolamento atomicidade durabilidade consistência normalização dependência "
    "redundância esquema relação atributo tupla vetor embedding similaridade cosseno "
    "grafo vizinho camada busca texto lexema radical dicionário planejador custo "
    "estatística página buffer disco memória cache log replicação backup restauração "
    "particionamento fragmento chave-estrangeira restrição gatilho função visão "
    "agregação janela ordenação agrupamento filtro predicado seletividade cardinalidade"
).split()
COMUNS = "o a de que em um uma para com por os as do da no na se mais como mas".split()


def url_psycopg() -> str:
    return get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")


def gerar_vetores(n: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    centros = rng.normal(size=(TOPICOS, EMBEDDING_DIM)).astype(np.float32)
    topico = rng.integers(0, TOPICOS, size=n)
    vetores = centros[topico] + rng.normal(scale=0.9, size=(n, EMBEDDING_DIM)).astype(np.float32)
    vetores /= np.linalg.norm(vetores, axis=1, keepdims=True)
    return vetores, topico


def gerar_texto(topico: int, rng: np.random.Generator) -> str:
    rng_topico = np.random.default_rng(topico)
    palavras_do_topico = rng_topico.choice(VOCABULARIO, size=8, replace=False)
    palavras = list(rng.choice(palavras_do_topico, size=25)) + list(rng.choice(COMUNS, size=35))
    rng.shuffle(palavras)
    return " ".join(palavras)


def main(total: int) -> None:
    rng = np.random.default_rng(42)
    n_grande = int(total * 0.8)
    n_pequenas = 20
    tamanhos = [n_grande] + [(total - n_grande) // n_pequenas] * n_pequenas

    with psycopg.connect(url_psycopg()) as conn:
        cur = conn.cursor()
        t0 = time.perf_counter()
        cur.execute("DELETE FROM usuarios WHERE email = %s", (EMAIL,))
        cur.execute(
            "INSERT INTO usuarios (nome, email) VALUES ('Experimento', %s) RETURNING id", (EMAIL,)
        )
        usuario_id = cur.fetchone()[0]

        materiais: list[tuple[int, int, int]] = []  # (material_id, disciplina_id, n_trechos)
        for i, tamanho in enumerate(tamanhos):
            nome = "Grande" if i == 0 else f"Pequena {i:02d}"
            cur.execute(
                "INSERT INTO disciplinas (usuario_id, nome) VALUES (%s, %s) RETURNING id",
                (usuario_id, nome),
            )
            disciplina_id = cur.fetchone()[0]
            for m in range(0, tamanho, TRECHOS_POR_MATERIAL):
                n = min(TRECHOS_POR_MATERIAL, tamanho - m)
                hash_ = hashlib.sha256(f"{disciplina_id}-{m}".encode()).hexdigest()
                cur.execute(
                    """INSERT INTO materiais
                         (disciplina_id, titulo, tipo, status, hash_sha256, tamanho_bytes)
                       VALUES (%s, %s, 'texto', 'concluido', %s, %s) RETURNING id""",
                    (disciplina_id, f"{nome} - parte {m // TRECHOS_POR_MATERIAL + 1}", hash_, n),
                )
                materiais.append((cur.fetchone()[0], disciplina_id, n))

        vetores, topicos = gerar_vetores(sum(tamanhos), rng)
        print(f"{sum(tamanhos)} vetores gerados em {time.perf_counter() - t0:.1f}s")

        cur.execute("DROP INDEX IF EXISTS ix_trechos_embedding_hnsw")
        t1 = time.perf_counter()
        linha = 0
        with cur.copy(
            "COPY trechos (material_id, disciplina_id, ordem, conteudo, pagina, pagina_fim,"
            " num_tokens, embedding) FROM STDIN"
        ) as copy:
            for material_id, disciplina_id, n in materiais:
                for ordem in range(n):
                    v = vetores[linha]
                    pagina = ordem // 3 + 1
                    copy.write_row(
                        (
                            material_id,
                            disciplina_id,
                            ordem,
                            gerar_texto(int(topicos[linha]), rng),
                            pagina,
                            pagina,
                            60,
                            "[" + ",".join(f"{x:.6f}" for x in v) + "]",
                        )
                    )
                    linha += 1
        t_copy = time.perf_counter() - t1
        print(f"COPY de {linha} trechos: {t_copy:.1f}s ({linha / t_copy:,.0f} linhas/s)")

        t2 = time.perf_counter()
        cur.execute("SET maintenance_work_mem = '512MB'")
        cur.execute("SET max_parallel_maintenance_workers = 2")
        cur.execute(
            """CREATE INDEX ix_trechos_embedding_hnsw ON trechos
               USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"""
        )
        print(f"CREATE INDEX hnsw: {time.perf_counter() - t2:.1f}s")
        conn.commit()

    # ANALYZE fora da transação da carga (precisa ver as linhas commitadas)
    with psycopg.connect(url_psycopg(), autocommit=True) as conn:
        conn.execute("ANALYZE trechos")
        conn.execute("ANALYZE materiais")
        tamanhos_sql = conn.execute(
            """SELECT pg_size_pretty(pg_table_size('trechos')),
                      pg_size_pretty(pg_relation_size('ix_trechos_embedding_hnsw')),
                      pg_size_pretty(pg_relation_size('ix_trechos_conteudo_tsv')),
                      pg_size_pretty(pg_relation_size('ix_trechos_disciplina_id'))"""
        ).fetchone()
    print(
        "tamanhos: tabela trechos={}, hnsw={}, gin={}, btree disciplina_id={}".format(
            *tamanhos_sql
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--trechos", type=int, default=50_000)
    main(parser.parse_args().trechos)
