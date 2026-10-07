from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.deps import DisciplinaDoUsuario, EmbedderDep, SessionDep
from app.schemas import ResultadoBusca
from app.servicos import busca

router = APIRouter(tags=["busca"])


@router.get("/disciplinas/{disciplina_id}/busca", response_model=list[ResultadoBusca])
def buscar(
    disciplina: DisciplinaDoUsuario,
    session: SessionDep,
    embedder: EmbedderDep,
    q: Annotated[str, Query(min_length=1, max_length=500, pattern=r"\S")],
    k: Annotated[int, Query(ge=1, le=50)] = 5,
    modo: Literal["semantica", "textual", "hibrida"] = "semantica",
):
    """Busca nos trechos dos materiais da disciplina.

    - semantica: por significado (embeddings + distância de cosseno).
    - textual: por palavras (full-text do Postgres, sem acento, com radicais).
    - hibrida: as duas combinadas por Reciprocal Rank Fusion.
    """
    q = q.strip()
    if modo == "textual":
        return busca.buscar_textual(session, disciplina.id, q, k)
    consulta = embedder.embed_consulta(q)
    if modo == "semantica":
        return busca.buscar_semantica(session, disciplina.id, consulta, k)
    return busca.buscar_hibrida(session, disciplina.id, q, consulta, k)
