"""Fixtures dos testes.

Os testes rodam num Postgres de verdade (banco estuda_ai_test), não em SQLite:
o SQLite não tem o tipo vector, nem índices parciais e CHECKs iguais aos do
Postgres. Testar num banco diferente do de produção esconde bugs.

Isolamento: cada teste roda dentro de uma transação que é desfeita (ROLLBACK)
no final, então os testes não enxergam os dados uns dos outros e o banco
volta limpo, sem precisar de TRUNCATE.
"""

from contextlib import contextmanager
from pathlib import Path

import pymupdf
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.deps import get_fabrica_sessao
from app.main import app
from app.models import Usuario
from app.servicos.embeddings import get_embedder
from tests.fakes import EmbedderFalso

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
def embedder():
    return EmbedderFalso()


@pytest.fixture
def pasta_uploads(tmp_path):
    return tmp_path / "uploads"


@pytest.fixture
def fabrica(session):
    """Fábrica de sessões para o processamento em background: entrega a sessão
    do teste, então tudo continua dentro da transação que será desfeita."""

    @contextmanager
    def _fabrica():
        yield session

    return _fabrica


@pytest.fixture
def client(session, embedder, pasta_uploads, fabrica):
    settings = get_settings().model_copy(update={"upload_dir": pasta_uploads, "max_upload_mb": 1})
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_embedder] = lambda: embedder
    app.dependency_overrides[get_fabrica_sessao] = lambda: fabrica
    # O TestClient só devolve a resposta depois de rodar as BackgroundTasks,
    # então ao fim de um client.post(...) o processamento já terminou.
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def criar_pdf(paginas: list[str]) -> bytes:
    """PDF com uma página por texto (insert_textbox quebra as linhas)."""
    doc = pymupdf.open()
    for texto in paginas:
        pagina = doc.new_page()
        pagina.insert_textbox(pymupdf.Rect(50, 50, 550, 800), texto, fontsize=9)
    return doc.tobytes()


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


@pytest.fixture
def disciplina_id(client, headers):
    return client.post("/disciplinas", json={"nome": "Banco de Dados"}, headers=headers).json()["id"]
