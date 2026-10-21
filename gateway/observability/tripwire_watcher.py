"""Forward new Tripwire integrity findings into the telemetry stream."""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from gateway.telemetry import EventType, emit_event

logger = logging.getLogger("agentshield.tripwire")

INTEGRITY_VERDICT_PATH = os.getenv(
    "INTEGRITY_VERDICT_PATH", "/var/lib/tripwire/status/verdict.json"
)
POLL_INTERVAL_SECONDS = max(1, int(os.getenv("TRIPWIRE_POLL_INTERVAL_SEC", "30")))
ALERT_REPEAT_SECONDS = max(1, int(os.getenv("TRIPWIRE_ALERT_REPEAT_SEC", "3600")))


class TripwireDriftWatcher:
    """Poll Tripwire verdicts and emit one alert per new drift state."""

    def __init__(
        self,
        verdict_path: str = INTEGRITY_VERDICT_PATH,
        poll_interval_seconds: int = POLL_INTERVAL_SECONDS,
        alert_repeat_seconds: int = ALERT_REPEAT_SECONDS,
    ) -> None:
        self.verdict_path = Path(verdict_path)
        self.poll_interval_seconds = max(1, poll_interval_seconds)
        self.alert_repeat_seconds = max(1, alert_repeat_seconds)
        self._last_fingerprint: Optional[tuple[Any, ...]] = None
        self._last_alert_signature: Optional[tuple[Any, ...]] = None
        self._last_alert_monotonic: Optional[float] = None
        self._is_running = False

    def read_verdict(self) -> dict[str, Any]:
        """Read one atomic verdict published by Tripwire."""
        try:
            with self.verdict_path.open("r", encoding="utf-8") as handle:
                verdict = json.load(handle)
        except FileNotFoundError:
            return {"_read_status": "absent"}
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Could not read Tripwire verdict: %s", exc)
            return {"_read_status": "error"}

        if not isinstance(verdict, dict):
            logger.error("Tripwire verdict must contain a JSON object")
            return {"_read_status": "error"}
        return verdict

    async def check_for_drift(self) -> None:
        """Queue an alert for a new violation state or a scheduled reminder."""
        verdict = self.read_verdict()
        if verdict.get("_read_status"):
            return

        checked_at = verdict.get("checked_at")
        status = verdict.get("verdict")
        reason = str(verdict.get("reason") or "")
        try:
            violations = int(verdict.get("violations", 0))
            objects = int(verdict.get("objects", 0))
        except (TypeError, ValueError):
            logger.error("Tripwire verdict has invalid object or violation counts")
            return

        fingerprint = (checked_at, status, violations, objects, reason)
        if fingerprint == self._last_fingerprint:
            return
        self._last_fingerprint = fingerprint

        if status == "clean" and violations == 0:
            self._last_alert_signature = None
            self._last_alert_monotonic = None
            return

        signature = (status, violations, objects, reason)
        now = time.monotonic()
        is_new_state = signature != self._last_alert_signature
        reminder_due = (
            self._last_alert_monotonic is not None
            and now - self._last_alert_monotonic >= self.alert_repeat_seconds
        )
        if not is_new_state and not reminder_due:
            return

        detail = reason or (
            f"{violations} of {objects} monitored objects differ from the approved baseline."
        )
        data = {
            "level": "CRITICAL",
            "verdict": status or "unknown",
            "violations": violations,
            "objects": objects,
            "checked_at": checked_at,
            "host": verdict.get("host"),
            "reason": detail,
        }
        emit_event(
            tenant_id="system",
            event_type=EventType.SYSTEM_DRIFT_DETECTED.value,
            data=data,
            source="agentshield-tripwire-watcher",
        )
        logger.critical(
            "Tripwire integrity alert: verdict=%s violations=%s objects=%s reason=%s",
            status,
            violations,
            objects,
            detail,
        )
        self._last_alert_signature = signature
        self._last_alert_monotonic = now

    async def start_background_watcher(self) -> None:
        """Poll until the application lifespan stops this watcher."""
        self._is_running = True
        logger.info(
            "Starting Tripwire verdict watcher, polling every %s seconds",
            self.poll_interval_seconds,
        )
        while self._is_running:
            try:
                await self.check_for_drift()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Tripwire watcher cycle failed")
            await asyncio.sleep(self.poll_interval_seconds)

    def stop(self) -> None:
        self._is_running = False
