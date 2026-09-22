from unittest.mock import patch
import os
import pytest

from config.settings import Settings, settings

PRODUCT_TIER_MAP = {
    "prod_VHXVVqTBAo3vfR": "starter",
    "prod_VHXYKG4I5wq1n6": "pro",
    "prod_VHYSzpIzdb1CsJ": "enterprise",
}


def test_default_values():
    s = Settings()
    assert s.app_name == "agent-shield"
    assert s.app_env == "development"
    assert s.log_level == "INFO"
    assert s.redis_url == "redis://localhost:6379/0"
    assert s.default_rate_limit_rpm == 60
    assert s.default_daily_budget_usd == 10.0


def test_stripe_defaults():
    with patch.dict(os.environ, {"STRIPE_API_KEY": "", "STRIPE_WEBHOOK_SECRET": ""}, clear=False):
        s = Settings()
    assert s.stripe_api_key == ""
    assert s.stripe_webhook_secret == ""
    assert s.stripe_success_url == "http://localhost:4200/billing/success"
    assert s.stripe_cancel_url == "http://localhost:4200/billing/cancel"
    assert s.stripe_product_starter == "prod_VHXVVqTBAo3vfR"
    assert s.stripe_product_pro == "prod_VHXYKG4I5wq1n6"
    assert s.stripe_product_enterprise == "prod_VHYSzpIzdb1CsJ"


def test_stripe_tier_rate_limits():
    s = Settings()
    assert s.default_free_rpm == 60
    assert s.starter_rpm == 20
    assert s.pro_rpm == 100
    assert s.enterprise_rpm == 1000


def test_env_override():
    with patch.dict(os.environ, {"APP_ENV": "production", "LOG_LEVEL": "WARNING"}):
        s = Settings()
    assert s.app_env == "production"
    assert s.log_level == "WARNING"


def test_env_override_stripe():
    with patch.dict(os.environ, {"STRIPE_API_KEY": "rk_test_override", "STRIPE_WEBHOOK_SECRET": "whsec_override"}):
        s = Settings()
    assert s.stripe_api_key == "rk_test_override"
    assert s.stripe_webhook_secret == "whsec_override"


def test_env_override_product_ids():
    with patch.dict(os.environ, {
        "STRIPE_PRODUCT_STARTER": "prod_new_starter",
        "STRIPE_PRODUCT_PRO": "prod_new_pro",
        "STRIPE_PRODUCT_ENTERPRISE": "prod_new_enterprise",
    }):
        s = Settings()
    assert s.stripe_product_starter == "prod_new_starter"
    assert s.stripe_product_pro == "prod_new_pro"
    assert s.stripe_product_enterprise == "prod_new_enterprise"


def test_settings_singleton():
    assert settings is not None
    assert isinstance(settings, Settings)


def test_missing_env_uses_defaults():
    with patch.dict(os.environ, {}, clear=False):
        s = Settings()
    assert s.app_name == "agent-shield"
    assert s.redis_url == "redis://localhost:6379/0"


def test_tier_map_completeness():
    s = Settings()
    tier_map = {
        s.stripe_product_starter: "starter",
        s.stripe_product_pro: "pro",
        s.stripe_product_enterprise: "enterprise",
    }
    assert len(tier_map) == 3
    for product_id, tier in PRODUCT_TIER_MAP.items():
        assert tier_map.get(product_id) == tier


def test_payment_env_vars_present():
    s = Settings()
    assert hasattr(s, "stripe_api_key")
    assert hasattr(s, "stripe_webhook_secret")
    assert hasattr(s, "stripe_success_url")
    assert hasattr(s, "stripe_cancel_url")


def test_tier_rpm_map_values():
    s = Settings()
    tier_map = {
        "free": s.default_free_rpm,
        "starter": s.starter_rpm,
        "pro": s.pro_rpm,
        "enterprise": s.enterprise_rpm,
    }
    assert tier_map["free"] == 60
    assert tier_map["starter"] == 20
    assert tier_map["pro"] == 100
    assert tier_map["enterprise"] == 1000
