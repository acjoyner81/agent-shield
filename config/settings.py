"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import model_validator


class Settings(BaseSettings):
    app_name: str = "agent-shield"
    app_env: str = "development"
    log_level: str = "INFO"
    auth0_domain: str = "dev-zymaiayb0afkpn7n.us.auth0.com"
    auth0_audience: str = "https://api.agentshield.local"
    auth0_issuer: str = "https://dev-zymaiayb0afkpn7n.us.auth0.com/"
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

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

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
