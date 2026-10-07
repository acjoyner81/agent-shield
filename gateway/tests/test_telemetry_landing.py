"""Telemetry landing tests (Spec 0014).

Recreates the suite lost as `test_telemetry_landing.py`, centred on AC-10:
replay is one atomic script, so a replay interrupted either before or after
the move leaves every entry in exactly one of the dead letter queue and the
stream. The admin gate is here too, because a replay anyone can trigger is a
way to drain a queue at will.
"""
import os

import pytest
from fastapi.testclient import TestClient

from gateway import main as main_module
from gateway.main import app

client = TestClient(app)

ADMIN_HEADERS = {"Authorization": "Bearer dev-mock-token"}  # permissions: telemetry:admin
UNPRIVILEGED_HEADERS = {"Authorization": "Bearer dev-unprivileged-token"}  # permissions: logs:read

DLQ_KEY = "telemetry:dlq"
STREAM_KEY = "telemetry:queue"


@pytest.fixture(autouse=True)
def enable_dev_mode():
    # The stand-in tokens are refused outside dev mode on purpose
    # (gateway/auth.py:50-66), so the redrive route cannot be reached by a
    # test without asking for the flag first.
    os.environ["DEV_MODE"] = "true"
    yield
    os.environ.pop("DEV_MODE", None)


class _ReplayInterrupted:
    """The endpoint's Redis client, dying on demand at one replay boundary."""

    def __init__(self, base, fail_on):
        self._base = base
        self._fail_on = fail_on

    def __getattr__(self, name):
        return getattr(self._base, name)

    async def eval(self, *args, **kwargs):
        if self._fail_on == "before the move":
            raise RuntimeError("connection lost before the replay moved anything")
        return await self._base.eval(*args, **kwargs)

    async def llen(self, *args, **kwargs):
        if self._fail_on == "after the move":
            raise RuntimeError("connection lost after the replay moved the entries")
        return await self._base.llen(*args, **kwargs)

    async def aclose(self):
        # The fake server outlives the endpoint's client on purpose: the test
        # reads both queues after the endpoint has returned.
        return None


def _replay_through_a_client_that_dies(monkeypatch, fail_on=None):
    base = main_module.aioredis.from_url("redis://fakeredis")
    monkeypatch.setattr(
        main_module.aioredis,
        "from_url",
        lambda *args, **kwargs: _ReplayInterrupted(base, fail_on),
    )


def test_a_replay_moves_the_whole_dead_letter_queue(fake_redis):
    """AC-10: every dead lettered entry lands on the stream, none left behind."""
    fake_redis.rpush(DLQ_KEY, '{"n":1}', '{"n":2}')

    response = client.post("/v1/telemetry/redrive", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["moved"] == 2
    assert body["remaining"] == 0
    assert fake_redis.lrange(DLQ_KEY, 0, -1) == []
    assert len(fake_redis.xrange(STREAM_KEY, "-", "+")) == 2


def test_a_replay_is_bounded_by_the_limit(fake_redis):
    """AC-9: the batch limit is honoured, and what stays behind is not lost."""
    fake_redis.rpush(DLQ_KEY, '{"n":1}', '{"n":2}', '{"n":3}')

    body = client.post(
        "/v1/telemetry/redrive", params={"limit": 2}, headers=ADMIN_HEADERS
    ).json()

    assert body["moved"] == 2
    assert body["remaining"] == 1
    assert len(fake_redis.lrange(DLQ_KEY, 0, -1)) == 1
    assert len(fake_redis.xrange(STREAM_KEY, "-", "+")) == 2


def test_a_replay_interrupted_before_the_move_leaves_every_entry_in_the_dead_letter_queue(
    fake_redis, monkeypatch
):
    """AC-10: a crash before the move loses nothing; the queue still holds it."""
    fake_redis.rpush(DLQ_KEY, '{"n":1}', '{"n":2}')
    _replay_through_a_client_that_dies(monkeypatch, fail_on="before the move")

    with pytest.raises(RuntimeError, match="before the replay moved"):
        client.post("/v1/telemetry/redrive", headers=ADMIN_HEADERS)

    assert fake_redis.lrange(DLQ_KEY, 0, -1) == ['{"n":1}', '{"n":2}']
    assert fake_redis.xrange(STREAM_KEY, "-", "+") == []


def test_a_replay_interrupted_after_the_move_leaves_every_entry_on_the_stream(
    fake_redis, monkeypatch
):
    """AC-10: a crash after the move loses nothing; the stream now holds it."""
    fake_redis.rpush(DLQ_KEY, '{"n":1}')
    _replay_through_a_client_that_dies(monkeypatch, fail_on="after the move")

    with pytest.raises(RuntimeError, match="after the replay moved"):
        client.post("/v1/telemetry/redrive", headers=ADMIN_HEADERS)

    assert fake_redis.lrange(DLQ_KEY, 0, -1) == []
    assert len(fake_redis.xrange(STREAM_KEY, "-", "+")) == 1


def test_a_caller_without_the_admin_scope_cannot_replay(fake_redis):
    """The route is gated: a replay is an admin operation, not a public one."""
    fake_redis.rpush(DLQ_KEY, '{"n":1}')

    response = client.post("/v1/telemetry/redrive", headers=UNPRIVILEGED_HEADERS)

    assert response.status_code == 403
    assert fake_redis.lrange(DLQ_KEY, 0, -1) == ['{"n":1}']  # untouched


def test_a_caller_without_a_credential_gets_401_not_a_replay(fake_redis):
    """A missing bearer token is a 401, and the queue is left as it was."""
    fake_redis.rpush(DLQ_KEY, '{"n":1}')

    response = client.post("/v1/telemetry/redrive")

    assert response.status_code == 401
    assert fake_redis.lrange(DLQ_KEY, 0, -1) == ['{"n":1}']  # untouched
