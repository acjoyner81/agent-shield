"""Application configuration loaded from environment variables."""

import os

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from gateway.config_loader import (
    GUARDRAILS_CONFIG,
    MODEL_PROVIDER_CONFIG,
)


class Settings(BaseSettings):
    app_name: str = "agent-shield"
    app_env: str = "development"
    log_level: str = "INFO"
    auth0_domain: str = "dev-zymaiayb0afkpn7n.us.auth0.com"
    auth0_audience: str = "https://api.agentshield.local"
    auth0_issuer: str = "https://dev-zymaiayb0afkpn7n.us.auth0.com/"
    # Machine-to-machine application the gateway uses to call mcp-server with a
    # client-credentials token (spec 0015.2, M-AC-8). The calling user's JWT is
    # never forwarded to mcp-server.
    auth0_m2m_client_id: str = ""
    auth0_m2m_client_secret: str = ""
    mcp_server_url: str = "http://mcp-server:8081/mcp/v1"
    baseten_api_key: str = Field(
        default_factory=lambda: os.getenv(MODEL_PROVIDER_CONFIG["api_key_env"], "")
    )
    meta_api_base_url: str = Field(
        default=MODEL_PROVIDER_CONFIG["base_url"], validation_alias="META_API_BASE_URL"
    )
    meta_default_model: str = MODEL_PROVIDER_CONFIG["default_model"]
    meta_request_timeout_seconds: float = MODEL_PROVIDER_CONFIG["timeout_seconds"]
    meta_prompt_injection_enabled: bool = GUARDRAILS_CONFIG["ingress"]["prompt_injection"]["enabled"]
    meta_pii_redaction_enabled: bool = GUARDRAILS_CONFIG["ingress"]["pii_redaction"]["enabled"]
    meta_pii_mask_patterns: list[str] = GUARDRAILS_CONFIG["ingress"]["pii_redaction"]["mask_patterns"]
    meta_max_tokens_limit: int = GUARDRAILS_CONFIG["ingress"]["max_tokens_limit"]
    meta_sanitize_reasoning_content: bool = GUARDRAILS_CONFIG["egress"]["sanitize_reasoning_content"]
    redis_url: str = "redis://localhost:6379/0"
    request_timeout_seconds: int = 60
    default_rate_limit_rpm: int = 60
    default_daily_budget_usd: float = 10.0
    mcp_server_transport: str = "stdio"
    prices_file: str = "config/prices.json"
    eval_enabled: bool = False
    eval_score_threshold: float = 0.85
    api_key_grace_period_seconds: int = 86400

    # Stripe Billing & Webhook Configuration
    # Opt-in switch. A key present in the environment is not the same as Stripe
    # being on (a test key often ships in a dev `.env`), so the health probe keys
    # off this flag, not the key, or a disabled Stripe pins the footer degraded.
    stripe_enabled: bool = False
    stripe_api_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_success_url: str = "http://localhost:4200/billing/success"
    stripe_cancel_url: str = "http://localhost:4200/billing/cancel"

    # Stripe Product Catalog Mapping
    stripe_product_starter: str = "prod_VHXVVqTBAo3vfR"
    stripe_product_pro: str = "prod_VHXYKG4I5wq1n6"
    stripe_product_enterprise: str = "prod_VHYSzpIzdb1CsJ"

    # Tier Rate Limits (RPM)
    default_free_rpm: int = 60
    starter_rpm: int = 20
    pro_rpm: int = 100
    enterprise_rpm: int = 1000

    model_config = SettingsConfigDict(
        env_file=".env", case_sensitive=False, extra="ignore", env_ignore_empty=True
    )

    @model_validator(mode="after")
    def _refuse_a_webhook_secret_that_cannot_be_one(self) -> "Settings":
        """Fail at startup when the webhook secret is unusable outside dev.

        The Stripe library refuses an empty secret, so a missing
        STRIPE_WEBHOOK_SECRET produces a 400 on every webhook rather than a
        forged one being accepted. That is safe and silent, which is its own
        problem: subscriptions stop updating, nothing in the logs says why, and
        an operator reads a healthy service and a stalled tenant list. Raising
        here turns that into an unmissable startup failure instead.

        Only dev and test are exempt. A placeholder that carries the real
        whsec_ prefix is worse than empty, because the library accepts it and
        anyone holding a published value can sign arbitrary events.
        """
        if self.app_env in ("development", "test"):
            return self
        secret = self.stripe_webhook_secret.strip()
        if not secret:
            raise ValueError(
                "STRIPE_WEBHOOK_SECRET is required when APP_ENV is not development or "
                "test. Without it every Stripe webhook is rejected and subscriptions "
                "silently stop updating."
            )
        if not secret.startswith("whsec_"):
            raise ValueError("STRIPE_WEBHOOK_SECRET must start with 'whsec_'.")
        return self

    def can_verify_webhooks(self) -> bool:
        """Whether a webhook signature can actually be verified right now."""
        secret = self.stripe_webhook_secret.strip()
        return bool(secret) and secret.startswith("whsec_")


settings = Settings()
