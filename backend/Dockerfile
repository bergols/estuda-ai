FROM python:3.12-slim

# uv instala as dependências a partir do uv.lock (versões travadas = build reproduzível)
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv

# O venv fica fora de /app para não ser apagado pelo volume de desenvolvimento
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copia só os manifestos primeiro: a camada de dependências fica em cache
# e só é refeita quando pyproject.toml/uv.lock mudam.
COPY pyproject.toml uv.lock ./
# --no-cache: o cache do uv (downloads) não fica gravado na camada da imagem.
# Produção passa UV_SYNC_ARGS=--no-dev (sem pytest/ruff na imagem).
ARG UV_SYNC_ARGS=""
RUN uv sync --frozen --no-install-project --no-cache ${UV_SYNC_ARGS}

# Usuário sem privilégios para a produção (o compose de produção roda com
# "user: 10001"). As pastas dos volumes já nascem dele: um volume novo copia o dono
# da pasta da imagem. O desenvolvimento continua como root (volumes antigos são do root).
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data/uploads /modelos \
    && chown app:app /data/uploads /modelos

COPY . .

EXPOSE 8000

# Aplica as migrations antes de subir a API: o schema do banco sempre
# acompanha a versão do código.
CMD ["sh", "-c", "alembic upgrade head && python -m scripts.papel_app && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
