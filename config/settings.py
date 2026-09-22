"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    eval_enabled: bool = False
    eval_score_threshold: float = 0.85

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


settings = Settings()