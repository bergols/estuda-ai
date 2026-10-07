"""Buscas em trechos: semântica (pgvector), textual (full-text) e híbrida (RRF).

O SQL fica escrito por extenso (text()) de propósito: é o que você vai ler no
EXPLAIN ANALYZE e é o objeto de estudo desta fase.
"""

from pgvector.sqlalchemy import Vector
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from app.models import CONFIG_TEXTO, EMBEDDING_DIM

# Constante do Reciprocal Rank Fusion. 60 é o valor do artigo original
# (Cormack et al., 2009) e o padrão de mercado; ver docs/busca-semantica.md.
RRF_K = 60

# ---------------------------------------------------------------- semântica
#
# <=> é a distância de cosseno do pgvector (0 = mesma direção, 2 = opostos).
# Como o índice HNSW foi criado com vector_cosine_ops, ORDER BY embedding <=> ...
# LIMIT k pode ser respondido pelo índice, sem calcular a distância para todas
# as linhas. score = 1 - distância = similaridade de cosseno.
#
# A subconsulta pega os k vizinhos ANTES do JOIN com materiais: o planejador
# vê um "ORDER BY distância LIMIT k" simples sobre trechos.
SQL_SEMANTICA = text("""
    SELECT v.id AS trecho_id, v.material_id, m.titulo AS material_titulo,
           v.conteudo, v.pagina, v.pagina_fim,
           1 - v.distancia AS score
    FROM (
        SELECT id, material_id, conteudo, pagina, pagina_fim,
               embedding <=> :consulta AS distancia
        FROM trechos
        WHERE disciplina_id = :disciplina_id
        ORDER BY distancia
        LIMIT :k
    ) AS v
    JOIN materiais AS m ON m.id = v.material_id
    ORDER BY v.distancia
""").bindparams(bindparam("consulta", type_=Vector(EMBEDDING_DIM)))

# ------------------------------------------------------------------ textual
#
# websearch_to_tsquery entende a sintaxe de buscador: "frase exata", OR, -excluir.
# Sem operadores, exige TODAS as palavras (AND): ótimo para termos exatos,
# ruim para perguntas longas em linguagem natural.
# @@ = "o tsvector casa com a tsquery", e é o que o índice GIN acelera.
# ts_rank_cd com normalização 32 => rank/(rank+1), um score entre 0 e 1.
SQL_TEXTUAL = text(f"""
    SELECT t.id AS trecho_id, t.material_id, m.titulo AS material_titulo,
           t.conteudo, t.pagina, t.pagina_fim,
           ts_rank_cd(t.conteudo_tsv, q.consulta, 32) AS score
    FROM trechos AS t
    JOIN materiais AS m ON m.id = t.material_id
    CROSS JOIN websearch_to_tsquery('{CONFIG_TEXTO}', :texto) AS q(consulta)
    WHERE t.disciplina_id = :disciplina_id
      AND t.conteudo_tsv @@ q.consulta
    ORDER BY score DESC, t.id
    LIMIT :k
""")

# ------------------------------------------------------------------ híbrida
#
# Reciprocal Rank Fusion: roda as duas buscas, pega a POSIÇÃO de cada trecho em
# cada lista (row_number(), uma window function) e soma 1/(60 + posição).
# Usa posições, não scores, porque cosseno (0..1) e ts_rank (outra escala) não
# são comparáveis; a posição é.
#
# Na perna textual, as palavras da pergunta são combinadas com OU (|) em vez de E:
# plainto_tsquery gera 'indic' & 'acelar', e trocamos & por |. Assim um trecho
# que tem só parte das palavras ainda entra na lista, e o ts_rank_cd põe na
# frente quem tem mais delas.
#
# FULL OUTER JOIN: um trecho pode estar só na lista semântica, só na textual ou
# nas duas; quem está nas duas soma as duas parcelas e sobe.
SQL_HIBRIDA = text(f"""
    WITH semantica AS (
        SELECT id, row_number() OVER (ORDER BY distancia) AS posicao
        FROM (
            SELECT id, embedding <=> :consulta AS distancia
            FROM trechos
            WHERE disciplina_id = :disciplina_id
            ORDER BY distancia
            LIMIT :candidatos
        ) AS s
    ),
    textual AS (
        SELECT id, row_number() OVER (ORDER BY rank DESC, id) AS posicao
        FROM (
            SELECT t.id, ts_rank_cd(t.conteudo_tsv, q.consulta, 32) AS rank
            FROM trechos AS t
            CROSS JOIN (
                SELECT replace(plainto_tsquery('{CONFIG_TEXTO}', :texto)::text, ' & ', ' | ')
                       ::tsquery AS consulta
            ) AS q
            WHERE t.disciplina_id = :disciplina_id
              AND t.conteudo_tsv @@ q.consulta
            ORDER BY rank DESC, t.id
            LIMIT :candidatos
        ) AS x
    ),
    fusao AS (
        SELECT coalesce(s.id, x.id) AS id,
               s.posicao AS posicao_semantica,
               x.posicao AS posicao_textual,
               coalesce(1.0 / (:rrf_k + s.posicao), 0)
             + coalesce(1.0 / (:rrf_k + x.posicao), 0) AS score
        FROM semantica AS s
        FULL OUTER JOIN textual AS x ON x.id = s.id
    )
    SELECT f.id AS trecho_id, t.material_id, m.titulo AS material_titulo,
           t.conteudo, t.pagina, t.pagina_fim,
           f.score, f.posicao_semantica, f.posicao_textual
    FROM fusao AS f
    JOIN trechos AS t ON t.id = f.id
    JOIN materiais AS m ON m.id = t.material_id
    ORDER BY f.score DESC, f.id
    LIMIT :k
""").bindparams(bindparam("consulta", type_=Vector(EMBEDDING_DIM)))


def _configurar_hnsw(session: Session, candidatos: int) -> None:
    """Ajusta o HNSW só para a transação atual.

    set_config(..., true) = SET LOCAL, mas aceita parâmetros (SET não aceita).
    - ef_search: tamanho da fila de candidatos durante a busca no grafo. O HNSW
      devolve no máximo ef_search linhas, então precisa ser >= ao LIMIT pedido.
    - iterative_scan (pgvector 0.8+): se o WHERE disciplina_id descartar parte
      dos vizinhos encontrados, o índice continua procurando em vez de devolver
      menos de k linhas. strict_order mantém a ordem exata por distância.
    """
    session.execute(
        text("SELECT set_config('hnsw.ef_search', :ef, true)"), {"ef": str(max(40, candidatos))}
    )
    session.execute(text("SELECT set_config('hnsw.iterative_scan', 'strict_order', true)"))


def buscar_semantica(session: Session, disciplina_id: int, consulta: list[float], k: int):
    _configurar_hnsw(session, k)
    return session.execute(
        SQL_SEMANTICA, {"consulta": consulta, "disciplina_id": disciplina_id, "k": k}
    ).mappings().all()


def buscar_textual(session: Session, disciplina_id: int, texto: str, k: int):
    return session.execute(
        SQL_TEXTUAL, {"texto": texto, "disciplina_id": disciplina_id, "k": k}
    ).mappings().all()


def buscar_hibrida(
    session: Session, disciplina_id: int, texto: str, consulta: list[float], k: int
):
    # Cada lista traz mais candidatos que k: um trecho em 8º lugar nas duas
    # listas pode terminar em 1º na fusão.
    candidatos = max(4 * k, 20)
    _configurar_hnsw(session, candidatos)
    return session.execute(
        SQL_HIBRIDA,
        {
            "consulta": consulta,
            "texto": texto,
            "disciplina_id": disciplina_id,
            "k": k,
            "candidatos": candidatos,
            "rrf_k": RRF_K,
        },
    ).mappings().all()
