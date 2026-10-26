"""Read time token price estimation for the admin dashboard (Spec 0011 AC-1).

Costs are never stored. They are recomputed on every usage summary read from
the rate card in `gateway/config/prices.json`, so editing the file changes the
next response. The card maps a model name to USD per 1k tokens.
"""

import yaml
import logging
import threading
from pathlib import Path
from typing import Dict, Optional

from config.settings import settings

logger = logging.getLogger("agentshield.pricing")

TOKENS_PER_PRICE_UNIT = 1000

_lock = threading.Lock()
_cache: Dict[str, float] = {}
_cache_path: Optional[Path] = None
_cache_mtime: Optional[float] = None


def _resolve_price_file() -> Optional[Path]:
    """Deprecated: Pricing now reads directly from the model registry."""
    return None


def load_prices() -> Dict[str, float]:
    """Read the rate card from the model registry YAML. A missing or malformed file yields no rates."""
    path = Path("gateway/models_registry.yaml")
    if not path.exists():
        logger.warning("No model registry found, token cost will report as 0.")
        return {}

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error(f"Failed to read registry file {path}: {exc}")
        return {}

    if not isinstance(raw, dict) or "models" not in raw:
        logger.error(f"Registry file {path} must contain a 'models' list.")
        return {}

    prices: Dict[str, float] = {}
    for m in raw["models"]:
        try:
            model_id = m["model_id"]
            # For the dashboard summary, we use a simple average of input and output costs
            avg_cost = (m["input_cost_per_1k"] + m["output_cost_per_1k"]) / 2
            prices[str(model_id)] = float(avg_cost)
        except (KeyError, TypeError, ValueError):
            logger.warning(f"Ignoring invalid price entry for model {m.get('model_id')} in {path}.")
    return prices


def get_prices(refresh: bool = False) -> Dict[str, float]:
    """
    Return the rate card, parsed once and reused until the file changes.

    The mtime check keeps steady state at a single parse while still letting a
    price list edit apply to the very next read (Spec 0011 key invariants).
    """
    global _cache, _cache_path, _cache_mtime

    with _lock:
        path = _resolve_price_file()
        try:
            mtime = path.stat().st_mtime if path else None
        except OSError:
            mtime = None

        if refresh or _cache_path != path or _cache_mtime != mtime:
            _cache = load_prices()
            _cache_path = path
            _cache_mtime = mtime
        return dict(_cache)


def price_per_1k(model: str) -> float:
    """Rate for a model, or 0 when the rate card has no entry for it."""
    return get_prices().get(model, 0.0)


def estimate_cost_usd(model: str, total_tokens: int) -> float:
    """Cost in USD for a model's tokens. An unpriced model costs 0, never an error."""
    if not total_tokens:
        return 0.0
    return round((total_tokens / TOKENS_PER_PRICE_UNIT) * price_per_1k(model), 6)
