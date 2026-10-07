from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

# O engine mantém um pool de conexões: abrir uma conexão no Postgres
# custa caro (um processo novo no servidor), então elas são reaproveitadas.
# pool_pre_ping testa a conexão antes de usar, evitando erro após o banco reiniciar.
engine = create_engine(get_settings().database_url, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """Dependência do FastAPI: uma sessão (e uma transação) por requisição."""
    with SessionLocal() as session:
        yield session


def constraint_violada(erro: IntegrityError) -> str | None:
    """Nome da constraint que o Postgres rejeitou (ex.: 'uq_disciplinas_usuario_nome').

    Por isso as constraints têm nomes previsíveis: a API traduz cada uma num
    erro HTTP específico.
    """
    diag = getattr(erro.orig, "diag", None)
    return getattr(diag, "constraint_name", None)
