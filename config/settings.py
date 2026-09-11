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

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")


settings = Settings()
