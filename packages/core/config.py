"""
Application Configuration

Centralized configuration using pydantic-settings.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_env: str = Field(default="development", alias="APP_ENV")
    app_debug: bool = Field(default=True, alias="APP_DEBUG")
    app_secret_key: str = Field(default="dev-secret-change-in-production", alias="APP_SECRET_KEY")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://socialerp:socialerp@localhost:5432/socialerp",
        alias="DATABASE_URL",
    )
    database_echo: bool = Field(default=False, alias="DATABASE_ECHO")

    # Redis
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    # Meta (WhatsApp/Instagram)
    meta_app_id: str = Field(default="", alias="META_APP_ID")
    meta_app_secret: str = Field(default="", alias="META_APP_SECRET")
    meta_verify_token: str = Field(default="", alias="META_VERIFY_TOKEN")
    meta_whatsapp_phone_number_id: str = Field(default="", alias="META_WHATSAPP_PHONE_NUMBER_ID")
    meta_instagram_account_id: str = Field(default="", alias="META_INSTAGRAM_ACCOUNT_ID")
    meta_graph_api_version: str = Field(default="v20.0", alias="META_GRAPH_API_VERSION")

    # Fake Meta (for testing)
    fake_meta_base_url: str = Field(default="http://localhost:8081", alias="FAKE_META_BASE_URL")

    # Daraja (M-Pesa)
    daraja_consumer_key: str = Field(default="", alias="DARAJA_CONSUMER_KEY")
    daraja_consumer_secret: str = Field(default="", alias="DARAJA_CONSUMER_SECRET")
    daraja_passkey: str = Field(default="", alias="DARAJA_PASSKEY")
    daraja_shortcode: str = Field(default="", alias="DARAJA_SHORTCODE")
    daraja_environment: str = Field(default="sandbox", alias="DARAJA_ENVIRONMENT")
    daraja_base_url: str = Field(default="https://sandbox.safaricom.co.ke", alias="DARAJA_BASE_URL")
    daraja_callback_url: str = Field(default="", alias="DARAJA_CALLBACK_URL")

    # Fake Daraja (for testing)
    fake_daraja_base_url: str = Field(default="http://localhost:8082", alias="FAKE_DARAJA_BASE_URL")

    # Payment Aggregators
    aggregator_api_key: str = Field(default="", alias="AGGREGATOR_API_KEY")
    aggregator_api_secret: str = Field(default="", alias="AGGREGATOR_API_SECRET")
    aggregator_webhook_secret: str = Field(default="", alias="AGGREGATOR_WEBHOOK_SECRET")
    aggregator_base_url: str = Field(default="", alias="AGGREGATOR_BASE_URL")
    aggregator_callback_url: str = Field(default="", alias="AGGREGATOR_CALLBACK_URL")
    aggregator_name: str = Field(default="generic", alias="AGGREGATOR_NAME")
    aggregator_allowed_callback_ips: str = Field(
        default="", alias="AGGREGATOR_ALLOWED_CALLBACK_IPS"
    )

    # LLM Gateway
    llm_provider: str = Field(default="local", alias="LLM_PROVIDER")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_base_url: str = Field(default="http://localhost:11434", alias="LLM_BASE_URL")
    llm_model: str = Field(default="llama3.1:8b", alias="LLM_MODEL")
    llm_max_tokens: int = Field(default=4096, alias="LLM_MAX_TOKENS")
    llm_temperature: float = Field(default=0.1, alias="LLM_TEMPERATURE")
    llm_timeout_seconds: int = Field(default=30, alias="LLM_TIMEOUT_SECONDS")
    llm_per_tenant_daily_cap_usd: float = Field(default=5.0, alias="LLM_PER_TENANT_DAILY_CAP_USD")

    # Observability
    otel_service_name: str = Field(default="socialerp", alias="OTEL_SERVICE_NAME")
    otel_exporter_otlp_endpoint: str = Field(
        default="http://localhost:4317", alias="OTEL_EXPORTER_OTLP_ENDPOINT"
    )

    # Feature Flags
    feature_tiktok_enabled: bool = Field(default=False, alias="FEATURE_TIKTOK_ENABLED")
    feature_instagram_enabled: bool = Field(default=False, alias="FEATURE_INSTAGRAM_ENABLED")
    feature_pay_on_delivery_enabled: bool = Field(
        default=True, alias="FEATURE_PAY_ON_DELIVERY_ENABLED"
    )
    feature_owner_console_enabled: bool = Field(default=True, alias="FEATURE_OWNER_CONSOLE_ENABLED")

    # Tenant Onboarding
    onboarding_wizard_enabled: bool = Field(default=True, alias="ONBOARDING_WIZARD_ENABLED")
    default_autonomy_level: str = Field(default="L0", alias="DEFAULT_AUTONOMY_LEVEL")

    # Security
    cors_allowed_origins: str = Field(
        default="http://localhost:3000,http://localhost:8080", alias="CORS_ALLOWED_ORIGINS"
    )
    rate_limit_requests_per_minute: int = Field(default=100, alias="RATE_LIMIT_REQUESTS_PER_MINUTE")
    webhook_signature_tolerance_seconds: int = Field(
        default=300, alias="WEBHOOK_SIGNATURE_TOLERANCE_SECONDS"
    )

    # Testing
    test_database_url: str = Field(
        default="postgresql+asyncpg://socialerp:socialerp@localhost:5432/socialerp_test",
        alias="TEST_DATABASE_URL",
    )
    test_redis_url: str = Field(default="redis://localhost:6379/1", alias="TEST_REDIS_URL")

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",")]


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
