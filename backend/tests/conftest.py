"""Fixtures dos testes.

Os testes rodam num Postgres de verdade (banco estuda_ai_test), não em SQLite:
o SQLite não tem o tipo vector, nem índices parciais e CHECKs iguais aos do
Postgres. Testar num banco diferente do de produção esconde bugs.

Isolamento: cada teste roda dentro de uma transação que é desfeita (ROLLBACK)
no final, então os testes não enxergam os dados uns dos outros e o banco
volta limpo, sem precisar de TRUNCATE.

Dois papéis, como em produção: as migrations rodam como DONO (estuda_ai) e os
testes conectam como estuda_ai_app, o papel de menor privilégio da API. Assim um
teste que dependesse de um privilégio que a API não tem falharia aqui, e não só
depois do deploy. `engine_dono` existe para os poucos testes de administração.
"""

import base64
from contextlib import contextmanager
from pathlib import Path

import httpx
import pymupdf
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.deps import exigir_client_id, get_fabrica_sessao, get_llm, get_spotify
from app.main import app
from app.models import Usuario
from app.servicos.auth import emitir_token
from app.servicos.embeddings import get_embedder
from app.servicos.llm import ClienteLLM
from app.servicos.spotify import ClienteSpotify
from tests.fakes import AnthropicFalso, EmbedderFalso, SpotifyFalso

MODELO_TESTE = "claude-haiku-4-5-20251001"
PAPEL_APP = "estuda_ai_app"

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _no_banco_de_teste(url: str):
    url = make_url(url)
    return url.set(database=f"{url.database}_test")


def urls_teste():
    """(papel da aplicação, dono), ambos apontando para <banco>_test."""
    settings = get_settings()
    dono = settings.migration_database_url or settings.database_url
    return _no_banco_de_teste(settings.database_url), _no_banco_de_teste(dono)


@pytest.fixture(scope="session")
def engine_dono():
    _, url = urls_teste()

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

    # O schema de teste é criado pela MESMA migration da produção, como dono.
    # downgrade -> upgrade garante um banco limpo e testa os dois sentidos.
    cfg = Config(BACKEND_DIR / "alembic.ini")
    cfg.attributes["url"] = url.render_as_string(hide_password=False)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def engine(engine_dono):
    """Conexões da API nos testes: papel estuda_ai_app (sem DDL, sem DELETE no histórico)."""
    url_app, _ = urls_teste()
    if url_app.username != PAPEL_APP:
        pytest.exit(f"DATABASE_URL deveria usar o papel {PAPEL_APP}; veja o .env.example")
    # Papéis valem para o servidor inteiro: a senha é a mesma do banco de desenvolvimento.
    # Aplicada aqui também para os testes não dependerem de o compose ter rodado papel_app.
    with engine_dono.connect() as conn:
        conn.execute(
            text(f"ALTER ROLE {PAPEL_APP} LOGIN PASSWORD " + _literal(conn, url_app.password))
        )
        conn.commit()
    engine = create_engine(url_app)
    yield engine
    engine.dispose()


def _literal(conn, valor: str) -> str:
    """ALTER ROLE não aceita bind parameter; quote_literal() escapa no próprio Postgres."""
    return conn.scalar(text("SELECT quote_literal(:v)"), {"v": valor})


@pytest.fixture
def session(engine):
    yield from _sessao_desfeita(engine)


@pytest.fixture
def session_dono(engine_dono):
    """Sessão como DONO do schema, também desfeita no fim. Só para testes de admin."""
    yield from _sessao_desfeita(engine_dono)


def _sessao_desfeita(engine):
    with engine.connect() as conn:
        transacao = conn.begin()
        # create_savepoint: cada session.commit() da aplicação vira um
        # RELEASE SAVEPOINT dentro da transação externa, e cada rollback()
        # volta só até o savepoint. No fim, a transação externa é desfeita.
        sessao = Session(bind=conn, join_transaction_mode="create_savepoint")
        yield sessao
        # Como o teste nunca dá COMMIT, as constraints adiadas (ex.: "questão de
        # múltipla escolha precisa de 2+ alternativas e 1 correta") nunca seriam
        # verificadas. Forçamos a verificação aqui: se a aplicação deixou dados que
        # o COMMIT recusaria, o teste falha.
        if transacao.is_active and not sessao.in_nested_transaction():
            sessao.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
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
def anthropic_falso():
    """API da Anthropic simulada. Cada teste coloca as respostas em .roteiro;
    se o código chamar o LLM sem roteiro, o teste falha (nenhuma chamada real)."""
    return AnthropicFalso()


@pytest.fixture
def spotify_falso():
    """Spotify simulado (tokens + Web API); nenhum teste fala com o Spotify real."""
    return SpotifyFalso()


@pytest.fixture
def settings_teste(pasta_uploads):
    """Configurações usadas pela API nos testes; um teste pode mudar um campo
    (ex.: settings_teste.limite_geracoes_dia = 2) antes de chamar a rota."""
    return get_settings().model_copy(update={
        "upload_dir": pasta_uploads, "max_upload_mb": 1,
        # Chave fixa só de teste (32 bytes zero em base64); nunca use algo assim de verdade
        "cifra_chaves": SecretStr("1:" + base64.b64encode(bytes(32)).decode()),
        "spotify_client_id": "client-id-de-teste",
    })


@pytest.fixture
def client(session, embedder, fabrica, anthropic_falso, spotify_falso, settings_teste):
    settings = settings_teste
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_embedder] = lambda: embedder
    app.dependency_overrides[get_fabrica_sessao] = lambda: fabrica
    app.dependency_overrides[get_llm] = lambda: ClienteLLM(MODELO_TESTE, cliente=anthropic_falso)
    app.dependency_overrides[get_spotify] = lambda: ClienteSpotify(
        exigir_client_id(settings), httpx.Client(transport=spotify_falso.transport))
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


def cabecalho(usuario) -> dict:
    """Authorization com um JWT válido para o usuário (sem passar pelo login, que
    custaria um hash argon2 por teste; o login tem testes próprios em test_auth.py)."""
    return {"Authorization": f"Bearer {emitir_token(usuario).valor}"}


@pytest.fixture
def headers(usuario):
    return cabecalho(usuario)


@pytest.fixture
def disciplina_id(client, headers):
    return client.post("/disciplinas", json={"nome": "Banco de Dados"}, headers=headers).json()["id"]
