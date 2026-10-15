"""
Settings management for SocialERP.

Uses Pydantic Settings for type-safe configuration with environment variable support.
All secrets must come from environment variables, never from config files.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # -------------------------------------------------------------------------
    # Application
    # -------------------------------------------------------------------------
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "DEBUG"
    SECRET_KEY: str = Field(..., min_length=32)
    ENCRYPTION_KEY: str = Field(..., min_length=32)

    # -------------------------------------------------------------------------
    # Database
    # -------------------------------------------------------------------------
    POSTGRES_USER: str = "socialerp"
    POSTGRES_PASSWORD: str = "socialerp_dev_password"
    POSTGRES_DB: str = "socialerp"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432

    @computed_field  # type: ignore[prop-decorator]  # pydantic computed_field stacked on @property is unsupported by mypy
    @property
    def DATABASE_URL(self) -> PostgresDsn:  # ruff: ignore[invalid-function-name]
        return PostgresDsn.build(
            scheme="postgresql+asyncpg",
            username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD,
            host=self.POSTGRES_HOST,
            port=self.POSTGRES_PORT,
            path=self.POSTGRES_DB,
        )

    # -------------------------------------------------------------------------
    # Redis
    # -------------------------------------------------------------------------
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379

    @computed_field  # type: ignore[prop-decorator]  # pydantic computed_field stacked on @property is unsupported by mypy
    @property
    def REDIS_URL(self) -> RedisDsn:  # ruff: ignore[invalid-function-name]
        return RedisDsn.build(
            scheme="redis",
            host=self.REDIS_HOST,
            port=self.REDIS_PORT,
            path="/0",
        )

    # -------------------------------------------------------------------------
    # Meta (WhatsApp + Instagram)
    # -------------------------------------------------------------------------
    META_APP_ID: str = ""
    META_APP_SECRET: str = ""
    META_WEBHOOK_VERIFY_TOKEN: str = ""
    META_WEBHOOK_URL: str = ""
    META_API_VERSION: str = "v20.0"
    META_GRAPH_URL: str = "https://graph.facebook.com"

    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_BUSINESS_ACCOUNT_ID: str = ""

    INSTAGRAM_PAGE_ID: str = ""
    INSTAGRAM_BUSINESS_ACCOUNT_ID: str = ""

    # -------------------------------------------------------------------------
    # Daraja (M-Pesa)
    # -------------------------------------------------------------------------
    DARAJA_CONSUMER_KEY: str = ""
    DARAJA_CONSUMER_SECRET: str = ""
    DARAJA_PASSKEY: str = ""
    DARAJA_SHORTCODE: str = "174379"
    DARAJA_BASE_URL: str = "https://sandbox.safaricom.co.ke"
    DARAJA_CALLBACK_URL: str = ""
    # Shared secret embedded in the STK callback path so we can attribute an
    # inbound callback to a tenant before any tenant context exists.
    DARAJA_CALLBACK_TOKEN: str = ""
    DARAJA_ALLOWED_CALLBACK_IPS: str = ""
    DARAJA_TIMEOUT_SECONDS: int = 30
    # Manual "pay to Till and confirm" fallback: shared secret an operator/staff
    # member presents when recording that a manual payment arrived.
    MANUAL_PAYMENT_TOKEN: str = ""
    MANUAL_PAYMENT_TILL: str = ""

    # -------------------------------------------------------------------------
    # Payment Aggregators
    # -------------------------------------------------------------------------
    PESAPAL_CONSUMER_KEY: str = ""
    PESAPAL_CONSUMER_SECRET: str = ""
    PESAPAL_IPN_URL: str = ""
    PESAPAL_ENVIRONMENT: Literal["sandbox", "production"] = "sandbox"

    INTASEND_PUBLIC_KEY: str = ""
    INTASEND_SECRET_KEY: str = ""
    INTASEND_WEBHOOK_URL: str = ""
    INTASEND_ENVIRONMENT: Literal["sandbox", "production"] = "sandbox"

    # -------------------------------------------------------------------------
    # LLM Gateway
    # -------------------------------------------------------------------------
    LLM_PROVIDER: Literal["local", "openai", "anthropic", "custom"] = "local"
    LLM_MODEL: str = "llama3.1:8b"
    LLM_BASE_URL: str = "http://localhost:11434/v1"
    LLM_API_KEY: str = ""
    LLM_MAX_TOKENS: int = 4096
    LLM_TEMPERATURE: float = 0.1
    LLM_TIMEOUT_SECONDS: int = 30
    LLM_PER_TENANT_DAILY_CAP_USD: float = 10.00

    # -------------------------------------------------------------------------
    # Observability
    # -------------------------------------------------------------------------
    OTEL_EXPORTER_OTLP_ENDPOINT: str = "http://localhost:4317"
    OTEL_SERVICE_NAME: str = "socialerp"
    OTEL_TRACES_SAMPLER: str = "parentbased_always_on"
    OTEL_METRICS_EXPORTER: str = "prometheus"
    PROMETHEUS_PORT: int = 9090

    # -------------------------------------------------------------------------
    # Feature Flags
    # -------------------------------------------------------------------------
    FEATURE_TIKTOK_ENABLED: bool = False
    FEATURE_INSTAGRAM_ENABLED: bool = True
    FEATURE_WHATSAPP_ENABLED: bool = True
    FEATURE_PAY_ON_DELIVERY_ENABLED: bool = True
    FEATURE_RIDER_TRACKING_ENABLED: bool = False
    FEATURE_AI_AGENT_L0: bool = True
    FEATURE_AI_AGENT_L1: bool = False
    FEATURE_AI_AGENT_L2: bool = False
    FEATURE_AI_AGENT_L3: bool = False

    # -------------------------------------------------------------------------
    # Onboarding
    # -------------------------------------------------------------------------
    ONBOARDING_WIZARD_ENABLED: bool = True
    ONBOARDING_TARGET_MINUTES: int = 30

    # -------------------------------------------------------------------------
    # Billing (SaaS)
    # -------------------------------------------------------------------------
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_PRICE_ID_STARTER: str = ""
    STRIPE_PRICE_ID_PROFESSIONAL: str = ""
    STRIPE_PRICE_ID_ENTERPRISE: str = ""

    # -------------------------------------------------------------------------
    # Email
    # -------------------------------------------------------------------------
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "noreply@socialerp.com"

    # -------------------------------------------------------------------------
    # Kenya Data Protection Act
    # -------------------------------------------------------------------------
    DPA_REGISTRATION_NUMBER: str = ""
    DPA_DATA_CONTROLLER_NAME: str = ""
    DPA_DATA_CONTROLLER_EMAIL: str = ""
    DPA_RETENTION_DAYS: int = 2555

    # -------------------------------------------------------------------------
    # Development Overrides
    # -------------------------------------------------------------------------
    DEV_FAKE_META_URL: str = "http://localhost:8080"
    DEV_FAKE_DARAJA_URL: str = "http://localhost:8081"
    DEV_DISABLE_SIGNATURE_VERIFICATION: bool = True
    DEV_AUTO_CREATE_TENANT: bool = True


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()  # type: ignore[call-arg]  # required fields are populated from env by BaseSettings


# For backwards compatibility and easy imports
settings = get_settings()
