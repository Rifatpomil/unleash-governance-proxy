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
    jwt_algorithm: str = Field(default="HS256", description="JWT algorithm (HS256, RS256, ES256, ...)")
    jwt_audience: Optional[str] = Field(default=None, description="JWT audience claim")
    jwt_issuer: Optional[str] = Field(default=None, description="JWT issuer claim")
    # When set, the auth layer validates tokens using keys fetched from this JWKS URL
    # (rotating identity-provider keys) instead of the static `jwt_secret`. The `kid`
    # header selects the key; keys are cached and refreshed on miss.
    jwt_jwks_url: Optional[str] = Field(
        default=None,
        description="JWKS URL for asymmetric verification (e.g. https://issuer/.well-known/jwks.json)",
    )
    jwt_jwks_cache_seconds: int = Field(
        default=300,
        ge=30,
        le=86400,
        description="TTL for cached JWKS keys",
    )

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
    llm_model: str = Field(
        default="gpt-4o-mini",
        description="OpenAI chat model used for all AI features",
    )
    llm_timeout_seconds: float = Field(
        default=15.0,
        ge=1.0,
        le=120.0,
        description="Per-request timeout for LLM calls",
    )
    llm_max_retries: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Retry count for transient LLM failures (429/5xx)",
    )
    llm_max_output_tokens: int = Field(
        default=400,
        ge=16,
        le=4096,
        description="Cap on tokens per LLM completion (cost guard)",
    )
    llm_monthly_budget_usd: float = Field(
        default=0.0,
        ge=0.0,
        description="Soft budget cap per process uptime in USD (0 = disabled)",
    )

    # Trusted proxy hops for X-Forwarded-For (0 = do not trust XFF at all)
    trusted_proxy_hops: int = Field(
        default=0,
        ge=0,
        le=10,
        description="Number of trusted proxy hops; XFF right-most N are trusted",
    )

    # Distributed rate limiter backend (optional). When set, slowapi uses Redis
    # so limits are shared across replicas. Leave empty for in-process only.
    rate_limit_storage_uri: Optional[str] = Field(
        default=None,
        description="slowapi storage URI, e.g. redis://redis:6379/0 or memcached://...",
    )

    # OpenTelemetry (optional). When the OTLP endpoint is set, the app emits
    # traces for FastAPI, httpx, and SQLAlchemy spans.
    otel_service_name: str = Field(default="unleash-governance-proxy")
    otel_exporter_otlp_endpoint: Optional[str] = Field(
        default=None,
        description="OTLP/HTTP endpoint, e.g. http://otel-collector:4318",
    )
    otel_traces_sampler_ratio: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Parent-based ratio sampler for traces",
    )

    # Audit hash chain: when enabled each audit row's hash commits to the prior row,
    # turning tampering into a detectable chain break. Verification endpoint is gated.
    audit_hash_chain_enabled: bool = Field(
        default=True,
        description="Compute SHA-256 hash chain on audit inserts",
    )

    @field_validator("unleash_base_url")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/") if v else v


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance."""
    return Settings()
