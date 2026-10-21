"""Load the gateway's model routing and guardrail configuration."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "agent_shield_config.yaml"
CONFIG_PATH = Path(os.getenv("AGENT_SHIELD_CONFIG_PATH", str(DEFAULT_CONFIG_PATH)))
SUPPORTED_PII_PATTERNS = {"email", "ssn", "api_key", "credit_card"}


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"AgentShield config field {field} must be a mapping")
    return value


def _positive_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"AgentShield config field {field} must be a positive number")
    return float(value)


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Read and validate the YAML configuration; fail closed on bad config."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"Could not read AgentShield config at {path}") from exc
    except yaml.YAMLError as exc:
        raise RuntimeError(f"AgentShield config at {path} is invalid YAML") from exc

    config = _mapping(raw, "root")
    models = _mapping(config.get("models"), "models")
    providers = _mapping(models.get("providers"), "models.providers")
    provider_name = models.get("default_provider")
    if not isinstance(provider_name, str) or not provider_name.strip():
        raise ValueError("AgentShield config field models.default_provider is required")
    provider = _mapping(providers.get(provider_name), f"models.providers.{provider_name}")
    for field in ("base_url", "api_key_env", "default_model"):
        if not isinstance(provider.get(field), str) or not provider[field].strip():
            raise ValueError(f"AgentShield config field models.providers.{provider_name}.{field} is required")
    if not provider["base_url"].startswith("https://"):
        raise ValueError("AgentShield model provider base_url must use HTTPS")
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", provider["api_key_env"]):
        raise ValueError("AgentShield api_key_env must be an uppercase environment variable name")
    provider["timeout_seconds"] = _positive_number(
        provider.get("timeout_seconds", 30),
        f"models.providers.{provider_name}.timeout_seconds",
    )

    guardrails = _mapping(config.get("guardrails"), "guardrails")
    ingress = _mapping(guardrails.get("ingress"), "guardrails.ingress")
    egress = _mapping(guardrails.get("egress", {}), "guardrails.egress")
    injection = _mapping(ingress.get("prompt_injection"), "guardrails.ingress.prompt_injection")
    pii = _mapping(ingress.get("pii_redaction"), "guardrails.ingress.pii_redaction")
    for field, item, action in (
        ("prompt_injection", injection, "block"),
        ("pii_redaction", pii, "redact"),
    ):
        if not isinstance(item.get("enabled"), bool) or item.get("action") != action:
            raise ValueError(f"{field} must set a boolean enabled value and action '{action}'")
    patterns = pii.get("mask_patterns", [])
    if not isinstance(patterns, list) or any(
        not isinstance(pattern, str) or pattern not in SUPPORTED_PII_PATTERNS
        for pattern in patterns
    ):
        raise ValueError("PII mask_patterns must be a list of supported pattern names")
    max_tokens = ingress.get("max_tokens_limit", 4096)
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
        raise ValueError("guardrails.ingress.max_tokens_limit must be a positive integer")
    sanitize = egress.get("sanitize_reasoning_content", True)
    if not isinstance(sanitize, bool):
        raise ValueError("guardrails.egress.sanitize_reasoning_content must be boolean")

    return config


AGENT_SHIELD_CONFIG = load_config()
DEFAULT_PROVIDER_NAME = AGENT_SHIELD_CONFIG["models"]["default_provider"]
MODEL_PROVIDER_CONFIG = AGENT_SHIELD_CONFIG["models"]["providers"][DEFAULT_PROVIDER_NAME]
GUARDRAILS_CONFIG = AGENT_SHIELD_CONFIG["guardrails"]
