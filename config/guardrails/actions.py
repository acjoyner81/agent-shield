"""Custom guardrail actions; provider integrations will be added in Phase 2."""

import re


_EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")


def mask_pii(value: str) -> str:
    """Mask email addresses before telemetry or external model calls."""
    return _EMAIL_PATTERN.sub("[REDACTED_EMAIL]", value)


def moderate_input(value: str) -> bool:
    """Return whether input passes the initial local moderation boundary."""
    return bool(value.strip())
