"""Application configuration with environment variable support."""

from functools import lru_cache
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Server
    host: str = Field(default="0.0.0.0", description="Bind host")
    port: int = Field(default=8080, ge=1, le=65535, description="Bind port")

    # Database
    database_url: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/unleash_governance",
        description="PostgreSQL connection URL",
    )

    # JWT Auth
    jwt_secret: str = Field(
        default="change-me-in-production-use-secure-secret",
        description="Secret for JWT verification",
    )
    jwt_algorithm: str = Field(default="HS256", description="JWT algorithm")
    jwt_audience: Optional[str] = Field(default=None, description="JWT audience claim")
    jwt_issuer: Optional[str] = Field(default=None, description="JWT issuer claim")

    # Unleash
    unleash_base_url: str = Field(
        default="http://localhost:4242",
        description="Unleash server base URL",
    )
    unleash_api_token: str = Field(
        default="",
        description="Unleash Admin API token (required for apply)",
    )

    # OpenFGA (optional)
    openfga_api_url: Optional[str] = Field(
        default=None,
        description="OpenFGA API URL (e.g. http://localhost:8080)",
    )
    openfga_store_id: Optional[str] = Field(default=None, description="OpenFGA store ID")
    openfga_model_id: Optional[str] = Field(default=None, description="OpenFGA authorization model ID")

    # Local policy fallback (when OpenFGA not configured)
    policy_file_path: str = Field(
        default="policies/allowlist.yaml",
        description="Path to local allowlist policy file",
    )

    # Rate limiting (requests per minute per IP)
    rate_limit_per_minute: int = Field(
        default=60,
        ge=1,
        le=1000,
        description="Max requests per minute per IP (0 to disable)",
    )

    # Idempotency
    idempotency_ttl_seconds: int = Field(
        default=86400,  # 24 hours
        ge=60,
        le=604800,  # 7 days
        description="How long to retain idempotency keys",
    )

    # AI / LLM (optional)
    openai_api_key: Optional[str] = Field(
        default=None,
        description="OpenAI API key for AI features (summarization, insights, NL query)",
    )
    ai_features_enabled: bool = Field(
        default=True,
        description="Enable AI-powered features when API key is set",
    )

    @field_validator("unleash_base_url")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/") if v else v


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance."""
    return Settings()
