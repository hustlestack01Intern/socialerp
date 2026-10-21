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
    # Public order-tracking capability tokens (U29): HMAC key + TTL for the
    # signed `?t=` token on the public tracking endpoints. When the secret is
    # empty, SECRET_KEY is used as the signing key.
    TRACKING_TOKEN_SECRET: str = ""
    TRACKING_TOKEN_TTL_DAYS: int = 30
    # Tenant-local timezone used when deriving local-time CRM traits
    # (e.g. `typical_order_hour`). AGENTS.md targets Nairobi SMEs, so the
    # default is Kenya's zone (EAT = UTC+3, no DST).
    TENANT_TIMEZONE: str = "Africa/Nairobi"

    # -------------------------------------------------------------------------
    # Database
    # -------------------------------------------------------------------------
    POSTGRES_USER: str = "socialerp"
    # Must match the compose/CI database password ("socialerp"): a mismatch
    # here reads like a broken database rather than a wrong credential (U72).
    POSTGRES_PASSWORD: str = "socialerp"
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
    # Generic aggregator settings (for Pesapal, IntaSend, etc.)
    AGGREGATOR_API_KEY: str = ""
    AGGREGATOR_API_SECRET: str = ""
    AGGREGATOR_WEBHOOK_SECRET: str = ""
    AGGREGATOR_BASE_URL: str = ""
    AGGREGATOR_CALLBACK_URL: str = ""
    AGGREGATOR_NAME: str = "generic"
    AGGREGATOR_ALLOWED_CALLBACK_IPS: str = ""

    # Pesapal-specific
    PESAPAL_CONSUMER_KEY: str = ""
    PESAPAL_CONSUMER_SECRET: str = ""
    PESAPAL_IPN_URL: str = ""
    PESAPAL_ENVIRONMENT: Literal["sandbox", "production"] = "sandbox"

    # IntaSend-specific
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
    # Tenancy / billing gates (trial spine, plan M1)
    # -------------------------------------------------------------------------
    # When True, `TenantContextMiddleware` is registered on the API: it fails
    # closed (401) on non-public paths without a valid bearer JWT and sets the
    # `app.current_tenant_id` GUC so Row-Level Security is active on the API
    # path (U23). Default False so dev/test keep today's behaviour; a production
    # startup guard refuses to boot prod with this off.
    TENANCY_MIDDLEWARE_ENABLED: bool = False
    # When True (default in production), the agent entitlement gate denies LLM
    # spend for tenants whose plan is expired/pending or over cap, replying with
    # a deterministic holding message + handoff (zero LLM calls). Emergency
    # escape hatch: set False to bypass the gate entirely.
    BILLING_ENTITLEMENT_ENABLED: bool = True
    # Trial length from channel connect (U60). One human-approved extension of
    # TRIAL_EXTENSION_DAYS is permitted.
    TRIAL_LENGTH_DAYS: int = 14
    TRIAL_EXTENSION_DAYS: int = 7
    # Length of a PAID period, in days, counted from the payment date (F3;
    # approved 2026-10-21). Monthly in advance, per docs/PRICING_STRATEGY.md.
    # A paid plan always has a period: without one the entitlement would never
    # end and the tenant could never be re-decided.
    BILLING_PAID_PERIOD_DAYS: int = 30
    # Invite-code gate on self-serve registration (M1.5). When on, a
    # registration must carry a valid invite code; the founder issues one code
    # per prospect. Must be ON in production (startup guard) so the first
    # cohort is invited rather than open to the internet (trial stacking, U68).
    INVITE_CODES_REQUIRED: bool = False
    # A tenant that signs up but never connects a channel stays `pending`; after
    # this many days the pending row expires to `cancelled` so a verified number
    # is not permanently blocked (M1.3).
    TRIAL_PENDING_EXPIRY_DAYS: int = 30
    # Requests per minute, per client IP, on the auth endpoints (register and
    # login). A registration answers 403 for a bad invite code and 201 for a
    # good one, so the status code alone is a probe; the limit makes probing
    # expensive. It is per process (see packages/core/middleware/rate_limit.py
    # for what that does and does not buy). 0 disables the limiter.
    RATE_LIMIT_REQUESTS_PER_MINUTE: int = 100

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
