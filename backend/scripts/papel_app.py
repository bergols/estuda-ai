"""Dá LOGIN e senha ao papel da aplicação (estuda_ai_app), a partir de DATABASE_URL.

    docker compose exec backend python -m scripts.papel_app

Roda como DONO (MIGRATION_DATABASE_URL), depois das migrations. A migration
papel_app_menor_privilegio cria o papel SEM login e sem senha (migrations não
conhecem segredos); este script lê usuário e senha de DATABASE_URL, a única fonte
da verdade, e aplica. É idempotente: rodar de novo só reaplica a mesma senha.
O compose roda isto a cada subida, logo depois do "alembic upgrade head".

ALTER ROLE ... PASSWORD é um comando utilitário: NÃO aceita bind parameters ($1).
Por isso o valor é composto com psycopg.sql.Literal, que escapa aspas corretamente.
Uma f-string aqui seria uma SQL injection esperando uma senha com aspas.
"""

import sys

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from app.config import get_settings

PAPEL = "estuda_ai_app"


def main() -> None:
    settings = get_settings()
    if not settings.migration_database_url:
        sys.exit("Defina MIGRATION_DATABASE_URL (o dono do schema).")
    app = make_url(settings.database_url)
    if app.username != PAPEL:
        sys.exit(f"DATABASE_URL deveria usar o papel {PAPEL} (está usando {app.username!r}).")
    if not app.password:
        sys.exit("DATABASE_URL não tem senha.")
    dono = settings.migration_database_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dono, autocommit=True) as conn:
        conn.execute(
            sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(PAPEL), sql.Literal(app.password)
            )
        )
    print(f"Papel {PAPEL}: LOGIN com a senha de DATABASE_URL.")


if __name__ == "__main__":
    main()
