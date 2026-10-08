from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuração lida de variáveis de ambiente (ou do .env na raiz)."""

    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    # A API conecta com o papel de MENOR privilégio (estuda_ai_app: só dados).
    database_url: str
    # Migrations e scripts de admin conectam com o DONO do schema (DDL).
    migration_database_url: str | None = None

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

    # Proteção de custo e de força bruta (fase 6).
    # Quantas gerações de IA por dia (no fuso do usuário), contadas na auditoria
    # (tabela geracoes), inclusive as que falharam: também custam. 0 = IA desligada.
    limite_geracoes_dia: int = Field(default=50, ge=0)
    # Rate limiting (janelas fixas, contadas no Postgres com UPSERT).
    limite_ia_por_minuto: int = Field(default=10, ge=1)
    limite_login_por_ip: int = Field(default=20, ge=1)  # a cada 15 minutos
    limite_login_por_email: int = Field(default=5, ge=1)  # a cada 15 minutos

    # Segredo compartilhado com o servidor do Next.js (BFF). Com o BFF no meio, toda
    # requisição chega do IP do Next; ele repassa o IP real em X-Cliente-IP, e a API só
    # acredita nesse header quando X-BFF-Segredo confere. Sem segredo configurado, o
    # header é ignorado (senão qualquer um forjaria o IP e driblaria o limite de login).
    bff_segredo: SecretStr | None = Field(default=None, min_length=32)

    # Spotify (fase 7). No fluxo PKCE o Client ID é PÚBLICO (vai na URL de autorização
    # que o navegador abre) e não existe client secret. Sem ele, as rotas do Spotify
    # respondem 503 "não configurado".
    spotify_client_id: str | None = None
    # Chaves que cifram os tokens do Spotify guardados no banco (app/servicos/cifra.py):
    # "versão:base64 de 32 bytes", separadas por vírgula; a PRIMEIRA cifra o que é novo,
    # as outras só decifram (rotação). Gere com: echo "1:$(openssl rand -base64 32)"
    cifra_chaves: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
