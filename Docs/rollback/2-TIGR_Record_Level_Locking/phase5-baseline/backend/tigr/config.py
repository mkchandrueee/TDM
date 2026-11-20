from functools import lru_cache

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TIGR_", env_file=".env", extra="ignore")

    env: str = "development"
    api_prefix: str = "/api/v1"
    database_url: str = "postgresql+psycopg:///TIGR?host=/tmp"
    cors_origins: str = "http://localhost:5174,http://127.0.0.1:5174"
    auth_mode: str = "development"
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    oidc_algorithms: str = "RS256"
    neo4j_uri: str | None = None
    neo4j_secret_ref: str | None = None
    file_roots: str = "docs/source_data"
    masking_secret: str | None = None
    identity_secret: str | None = None
    identity_key_version: str = "v1"
    entity_locking_enabled: bool = True
    inline_jobs: bool = True
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "auto"
    ollama_request_timeout_seconds: int = 480

    @property
    def oidc_algorithms_list(self) -> list[str]:
        return [algorithm.strip() for algorithm in self.oidc_algorithms.split(",") if algorithm.strip()]

    @property
    def oidc_configured(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_audience and self.oidc_jwks_url)

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def allowed_file_roots(self) -> list[str]:
        return [root.strip() for root in self.file_roots.split(",") if root.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
