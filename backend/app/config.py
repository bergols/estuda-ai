from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuração lida de variáveis de ambiente (ou do .env na raiz)."""

    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    database_url: str
    test_database_url: str | None = None

    upload_dir: Path = Path("/data/uploads")
    max_upload_mb: int = 20
    embedding_model: str = "intfloat/multilingual-e5-small"

    # A chave em si (ANTHROPIC_API_KEY) não passa por aqui: o SDK da Anthropic a lê
    # direto do ambiente. Assim ela nunca aparece num repr()/log das configurações.
    anthropic_model: str = "claude-haiku-4-5-20251001"

    # Autenticação. O segredo assina os JWT: quem o tiver forja tokens de qualquer
    # usuário. SecretStr esconde o valor em repr()/logs. Gere com: openssl rand -hex 32
    jwt_secret: SecretStr = Field(min_length=32)
    # Login dura 30 dias (praticidade no celular); "sair de todos" revoga na hora.
    jwt_dias: int = Field(default=30, ge=1, le=365)


@lru_cache
def get_settings() -> Settings:
    return Settings()
