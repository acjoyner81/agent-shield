"""Shared test fixtures.

Every route reaches Redis through a `get_redis_client` dependency that FastAPI
captures at import time, so patching the module attribute in a test does nothing
and the handler quietly reads whatever is on the developer's own Redis. The
fixtures here redirect every Redis path the app can take - dependency overrides,
module globals, and the two from-scratch client factories - at a per-test
fakeredis server, so no test can read or write the host store.
"""

import os

import pytest
import fakeredis
import redis
from redis import asyncio as aioredis

from gateway import auth, billing, keys, main, metering, rate_limit
from gateway.main import app

# Modules that expose a `get_redis_client` dependency to FastAPI.
DEPENDENCY_MODULES = (auth, keys, metering, rate_limit)

# Module-level clients built at import time, holding a live host connection.
CLIENT_GLOBALS = (auth, keys, rate_limit, main, billing)


def _client_provider(client):
    """A zero-argument callable returning `client`.

    FastAPI analyses an override's own signature, so a bound default
    (`lambda client=client: client`) would be read as a query parameter holding
    a Redis connection and fail to build the dependant.
    """

    def provide():
        return client

    return provide


@pytest.fixture
def fake_redis(monkeypatch):
    """The per-test fakeredis, wired into every Redis path the app resolves."""
    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=server, decode_responses=True)
    async_client = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    provide = _client_provider(client)

    def sync_from_url(url, *args, **kwargs):
        return client

    def async_from_url(url, *args, **kwargs):
        return async_client

    # Captured dependency callables: the override table is the only thing that
    # reaches them.
    for module in DEPENDENCY_MODULES:
        app.dependency_overrides[module.get_redis_client] = provide
        # Call-time paths (`verify_api_key`, `process_token_event`, the limiter)
        # read the module attribute instead.
        monkeypatch.setattr(module, "get_redis_client", provide)

    # `metering.get_redis_client` builds a fresh client on every call, so the
    # classmethod itself has to be redirected.
    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(sync_from_url))
    monkeypatch.setattr(aioredis, "from_url", staticmethod(async_from_url))

    for module in CLIENT_GLOBALS:
        if hasattr(module, "r"):
            monkeypatch.setattr(module, "r", client)
        if hasattr(module, "_r"):
            monkeypatch.setattr(module, "_r", client)

    yield client

    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def no_real_redis(monkeypatch):
    """Fail loudly if a test reaches a real Redis, rather than passing on host state."""
    def guard(url, *args, **kwargs):
        raise AssertionError(f"a test opened a real Redis connection to {url!r}")

    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(guard))
    monkeypatch.setattr(aioredis, "from_url", staticmethod(guard))
    yield


@pytest.fixture
def dev_mode():
    """Run the request with the Auth0 stand-in tokens enabled, then restore the env."""
    previous = os.environ.get("DEV_MODE")
    os.environ["DEV_MODE"] = "true"
    yield
    if previous is None:
        os.environ.pop("DEV_MODE", None)
    else:
        os.environ["DEV_MODE"] = previous


@pytest.fixture(autouse=True)
def clean_dependency_overrides():
    """A test that overrides a dependency must not leak it into the next test."""
    yield
    app.dependency_overrides.clear()
