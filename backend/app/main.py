from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import get_session
from app.routers import busca, disciplinas, materiais, perguntar, usuarios

app = FastAPI(title="estuda-ai", version="0.1.0")
app.include_router(usuarios.router)
app.include_router(disciplinas.router)
app.include_router(materiais.router)
app.include_router(busca.router)
app.include_router(perguntar.router)


@app.get("/health")
def health(session: Annotated[Session, Depends(get_session)]):
    """Verifica se a API alcança o banco e se a extensão pgvector está ativa."""
    try:
        session.execute(text("SELECT 1"))
        pgvector = session.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        ).scalar_one_or_none()
    except SQLAlchemyError:
        return JSONResponse(status_code=503, content={"status": "erro", "banco": "indisponivel"})
    return {"status": "ok", "banco": "ok", "pgvector": pgvector}
