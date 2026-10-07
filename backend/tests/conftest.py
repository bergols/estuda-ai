"""Fixtures dos testes.

Os testes rodam num Postgres de verdade (banco estuda_ai_test), não em SQLite:
o SQLite não tem o tipo vector, nem índices parciais e CHECKs iguais aos do
Postgres. Testar num banco diferente do de produção esconde bugs.

Isolamento: cada teste roda dentro de uma transação que é desfeita (ROLLBACK)
no final, então os testes não enxergam os dados uns dos outros e o banco
volta limpo, sem precisar de TRUNCATE.
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.main import app
from app.models import Usuario

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _url_banco_teste():
    settings = get_settings()
    if settings.test_database_url:
        return make_url(settings.test_database_url)
    url = make_url(settings.database_url)
    return url.set(database=f"{url.database}_test")


@pytest.fixture(scope="session")
def engine():
    url = _url_banco_teste()

    # CREATE DATABASE não pode rodar dentro de uma transação, daí o AUTOCOMMIT.
    # Conectamos ao banco "postgres" (sempre existe) para criar o de teste.
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        existe = conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :nome"), {"nome": url.database}
        )
        if not existe:
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()

    # O schema de teste é criado pela MESMA migration da produção.
    # downgrade -> upgrade garante um banco limpo e testa os dois sentidos.
    cfg = Config(BACKEND_DIR / "alembic.ini")
    cfg.attributes["url"] = url.render_as_string(hide_password=False)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine):
    with engine.connect() as conn:
        transacao = conn.begin()
        # create_savepoint: cada session.commit() da aplicação vira um
        # RELEASE SAVEPOINT dentro da transação externa, e cada rollback()
        # volta só até o savepoint. No fim, a transação externa é desfeita.
        sessao = Session(bind=conn, join_transaction_mode="create_savepoint")
        yield sessao
        sessao.close()
        transacao.rollback()


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _criar_usuario(session: Session, nome: str, email: str) -> Usuario:
    usuario = Usuario(nome=nome, email=email)
    session.add(usuario)
    session.flush()  # envia o INSERT (e recebe o id) sem encerrar a transação
    return usuario


@pytest.fixture
def usuario(session):
    return _criar_usuario(session, "Ana", "ana@furg.br")


@pytest.fixture
def outro_usuario(session):
    return _criar_usuario(session, "Bruno", "bruno@furg.br")


@pytest.fixture
def headers(usuario):
    return {"X-Usuario-Id": str(usuario.id)}
