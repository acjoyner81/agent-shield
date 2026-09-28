"""Read time token price estimation for the admin dashboard (Spec 0011 AC-1).

Costs are never stored. They are recomputed on every usage summary read from
the rate card in `gateway/config/prices.json`, so editing the file changes the
next response. The card maps a model name to USD per 1k tokens.
"""

import json
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
    """Locate the rate card, preferring the configured path."""
    configured = Path(settings.prices_file)
    candidates = []
    if configured.is_absolute():
        candidates.append(configured)
    else:
        candidates.append(Path.cwd() / configured)
        candidates.append(Path(__file__).resolve().parent / "config" / configured.name)

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def load_prices() -> Dict[str, float]:
    """Read the rate card from disk. A missing or malformed file yields no rates."""
    path = _resolve_price_file()
    if path is None:
        logger.warning("No price file found, token cost will report as 0.")
        return {}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error(f"Failed to read price file {path}: {exc}")
        return {}

    if not isinstance(raw, dict):
        logger.error(f"Price file {path} must map model name to a number.")
        return {}

    prices: Dict[str, float] = {}
    for model, rate in raw.items():
        try:
            prices[str(model)] = float(rate)
        except (TypeError, ValueError):
            logger.warning(f"Ignoring non numeric rate for model '{model}' in {path}.")
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
