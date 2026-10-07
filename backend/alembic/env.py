from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.config import get_settings
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Usado pelo `alembic revision --autogenerate` e pelo `alembic check`
# para comparar os modelos com o banco.
target_metadata = Base.metadata


def get_url() -> str:
    # Os testes passam a URL do banco de teste via config; no resto, usa DATABASE_URL.
    return config.attributes.get("url") or get_settings().database_url


def run_migrations_offline() -> None:
    """Gera o SQL sem conectar (`alembic upgrade head --sql`): bom para revisar o DDL."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(get_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        # No Postgres o DDL é transacional: se um passo da migration falhar,
        # tudo é desfeito e o banco não fica pela metade.
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
