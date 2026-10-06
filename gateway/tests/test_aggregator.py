"""Aggregator DLQ regression tests.

The failure branch once called `r.eval(_DLQ_LUA_SCRIPT, ...)` with names that
did not exist anywhere in the module, so a Splunk outage crashed the aggregator
with NameError instead of dead-lettering the batch. The suite drives the real
`run_aggregator` failure path against fakeredis, not just the helper.
"""
import fakeredis
import pytest

import gateway.aggregator as aggregator


class StopLoop(Exception):
    """Raised out of `run_aggregator` once the scenario has run its course."""


@pytest.mark.asyncio
async def test_dlq_push_moves_batch_and_returns_depth():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    pushed, dropped, depth = await aggregator.dlq_push(r, ["a", "b", "c"], 100)
    assert (pushed, dropped, depth) == (3, 0, 3)
    assert await r.lrange(aggregator.DLQ_KEY, 0, -1) == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_dlq_push_trims_to_max_and_counts_drops():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await r.rpush(aggregator.DLQ_KEY, "old1", "old2", "old3", "old4", "old5")
    pushed, dropped, depth = await aggregator.dlq_push(r, ["n1", "n2", "n3"], 4)
    assert pushed == 3
    assert dropped == 4  # 5 + 3 = 8, trimmed back to 4
    assert depth == 4
    assert await r.llen(aggregator.DLQ_KEY) == 4
    assert await r.get(aggregator.DLQ_DROPPED_KEY) == "4"
    # Trim keeps the tail, so a replay popping the push end sees the newest entries.
    entries = await r.lrange(aggregator.DLQ_KEY, 0, -1)
    assert entries == ["old5", "n1", "n2", "n3"]


@pytest.mark.asyncio
async def test_dlq_push_empty_batch_is_a_noop():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    assert await aggregator.dlq_push(r, [], 10) == (0, 0, 0)


@pytest.mark.asyncio
async def test_failed_shipment_lands_in_dlq_instead_of_crashing(monkeypatch):
    """Regression: the DLQ path raised NameError on undefined script names."""
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(aggregator.redis, "from_url", lambda *a, **k: r)
    monkeypatch.setattr(aggregator, "BATCH_SIZE", 1)
    monkeypatch.setattr(aggregator, "MAX_RETRIES", 1)

    async def always_fails(client, logs):
        raise RuntimeError("splunk down")

    monkeypatch.setattr(aggregator, "ship_to_splunk", always_fails)
    monkeypatch.setattr(aggregator, "dump_to_stdout", _noop)

    await aggregator.ensure_consumer_group(r)
    await r.xadd(aggregator.QUEUE_KEY, {"payload": '{"level":"ERROR","message":"boom"}'})

    reads = {"n": 0}
    real_xreadgroup = r.xreadgroup

    async def xreadgroup_then_stop(*args, **kwargs):
        reads["n"] += 1
        if reads["n"] > 1:
            raise StopLoop
        return await real_xreadgroup(*args, **kwargs)

    monkeypatch.setattr(r, "xreadgroup", xreadgroup_then_stop)

    with pytest.raises(StopLoop):
        await aggregator.run_aggregator()

    entries = await r.lrange(aggregator.DLQ_KEY, 0, -1)
    assert entries == ['{"level":"ERROR","message":"boom"}']
    # The batch was acked: the DLQ copy is the only copy.
    pending = await r.xpending(aggregator.QUEUE_KEY, aggregator.GROUP_NAME)
    count = pending["pending"] if isinstance(pending, dict) else pending[0]
    assert count == 0


async def _noop(batch):
    return None
