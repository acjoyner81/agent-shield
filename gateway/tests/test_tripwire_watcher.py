import json

import pytest

from gateway.observability import tripwire_watcher as watcher_module
from gateway.observability.tripwire_watcher import TripwireDriftWatcher
from gateway.telemetry import EventType


def write_verdict(path, **changes):
    verdict = {
        "verdict": "clean",
        "objects": 150,
        "violations": 0,
        "checked_at": "2026-10-20T12:00:00Z",
        "host": "tripwire",
        "reason": "",
    }
    verdict.update(changes)
    path.write_text(json.dumps(verdict), encoding="utf-8")


@pytest.mark.asyncio
async def test_clean_verdict_does_not_emit_event(tmp_path, monkeypatch):
    path = tmp_path / "verdict.json"
    write_verdict(path)
    emitted = []
    monkeypatch.setattr(watcher_module, "emit_event", lambda **event: emitted.append(event))

    await TripwireDriftWatcher(str(path)).check_for_drift()

    assert emitted == []


@pytest.mark.asyncio
async def test_new_violation_emits_system_drift_event(tmp_path, monkeypatch):
    path = tmp_path / "verdict.json"
    write_verdict(
        path,
        verdict="violations",
        violations=2,
        reason="",
        checked_at="2026-10-20T12:05:00Z",
    )
    emitted = []
    monkeypatch.setattr(watcher_module, "emit_event", lambda **event: emitted.append(event))

    await TripwireDriftWatcher(str(path)).check_for_drift()

    assert len(emitted) == 1
    assert emitted[0]["tenant_id"] == "system"
    assert emitted[0]["event_type"] == EventType.SYSTEM_DRIFT_DETECTED.value
    assert emitted[0]["data"]["violations"] == 2


@pytest.mark.asyncio
async def test_same_violation_state_is_not_repeated_each_poll(tmp_path, monkeypatch):
    path = tmp_path / "verdict.json"
    write_verdict(
        path,
        verdict="violations",
        violations=1,
        checked_at="2026-10-20T12:05:00Z",
    )
    emitted = []
    monkeypatch.setattr(watcher_module, "emit_event", lambda **event: emitted.append(event))
    watcher = TripwireDriftWatcher(str(path), alert_repeat_seconds=3600)

    await watcher.check_for_drift()
    write_verdict(
        path,
        verdict="violations",
        violations=1,
        checked_at="2026-10-20T12:10:00Z",
    )
    await watcher.check_for_drift()

    assert len(emitted) == 1


@pytest.mark.asyncio
async def test_policy_mismatch_emits_alert_even_without_counted_violations(tmp_path, monkeypatch):
    path = tmp_path / "verdict.json"
    write_verdict(
        path,
        verdict="error",
        reason="Policy no longer matches the baseline.",
        checked_at="2026-10-20T12:05:00Z",
    )
    emitted = []
    monkeypatch.setattr(watcher_module, "emit_event", lambda **event: emitted.append(event))

    await TripwireDriftWatcher(str(path)).check_for_drift()

    assert len(emitted) == 1
    assert emitted[0]["data"]["reason"] == "Policy no longer matches the baseline."
