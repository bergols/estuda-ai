from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuração lida de variáveis de ambiente (ou do .env na raiz)."""

    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    database_url: str
    test_database_url: str | None = None

    upload_dir: Path = Path("/data/uploads")
    max_upload_mb: int = 20
    embedding_model: str = "intfloat/multilingual-e5-small"


@lru_cache
def get_settings() -> Settings:
    return Settings()
