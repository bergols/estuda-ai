"""Ordem do conteúdo: listar cards e questões na sequência em que a matéria aparece.

A posição de um card é a do PRIMEIRO trecho que ele cita: o material (na ordem em
que foi enviado: o id da identity cresce com o envio) e a posição do trecho dentro
dele (`trechos.ordem`). Card sem trecho (gerado por tema solto) vai para o fim.

LATERAL top-1, o mesmo recurso do analytics de foco (acerto_pos_sessao): para cada
card, uma subconsulta que pode olhar a linha de fora (`flashcard_id = flashcards.id`)
e devolve só a menor posição (ORDER BY ... LIMIT 1). Alternativa seria
`min(array[material_id, ordem])` num GROUP BY, que obriga a agrupar a linha inteira
do card; o LATERAL mantém a consulta principal como está e usa a PK de
flashcard_trechos (flashcard_id, trecho_id) para achar os trechos de cada card.
"""

from typing import Literal

from sqlalchemy import Select, Table, select, true

from app.models import Trecho

Ordem = Literal["conteudo", "recentes"]


def primeiro_trecho(associacao: Table, coluna: str, id_da_linha):
    """Subconsulta LATERAL com (material_id, ordem) do primeiro trecho citado."""
    return (
        select(Trecho.material_id.label("material_id"), Trecho.ordem.label("ordem"))
        .join(associacao, associacao.c.trecho_id == Trecho.id)
        .where(associacao.c[coluna] == id_da_linha)
        .order_by(Trecho.material_id, Trecho.ordem)
        .limit(1)
        .lateral("primeiro")
    )


def ordenar(consulta: Select, entidade, associacao: Table, coluna: str, ordem: Ordem) -> Select:
    if ordem == "recentes":
        return consulta.order_by(entidade.criado_em.desc(), entidade.id.desc())
    p = primeiro_trecho(associacao, coluna, entidade.id)
    # Desempate pelo id: dentro do mesmo trecho, na ordem em que foram criados
    return consulta.outerjoin(p, true()).order_by(
        p.c.material_id.asc().nulls_last(), p.c.ordem.asc().nulls_last(), entidade.id
    )
