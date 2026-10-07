"""Aggregator DLQ regression tests.

The failure branch once called `r.eval(_DLQ_LUA_SCRIPT, ...)` with names that
did not exist anywhere in the module, so a Splunk outage crashed the aggregator
with NameError instead of dead-lettering the batch. The suite drives the real
`run_aggregator` failure path against fakeredis, not just the helper.
"""
import json

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
    monkeypatch.setattr(aggregator, "SPLUNK_HEC_TOKEN", "test-collector-token")

    async def ca_ready():
        return True

    monkeypatch.setattr(aggregator, "wait_for_ca", ca_ready)

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


@pytest.mark.asyncio
async def test_run_aggregator_refuses_to_ship_without_a_ca(monkeypatch):
    """Verification is never skipped: no configured CA means no process."""
    monkeypatch.setattr(aggregator, "SPLUNK_HEC_TOKEN", "test-collector-token")
    monkeypatch.setattr(aggregator, "SPLUNK_HEC_CA", "")
    with pytest.raises(SystemExit):
        await aggregator.run_aggregator()


async def _noop(batch):
    return None


class _CollectorResponse:
    """A HEC answer: HTTP 200, with the real verdict in the body."""

    def __init__(self, body):
        self.status_code = 200
        self._body = body

    def json(self):
        return self._body

    @property
    def text(self):
        return json.dumps(self._body)

    def raise_for_status(self):
        return None


class _CollectorClient:
    """The client surface `ship_to_splunk` touches, answering as scripted."""

    def __init__(self, body):
        self._body = body

    async def post(self, *args, **kwargs):
        return _CollectorResponse(self._body)


@pytest.mark.asyncio
async def test_ship_to_splunk_raises_when_the_body_reports_a_nonzero_code():
    """AC-12: HTTP 200 is not success; the collector's own body code is."""
    with pytest.raises(aggregator.ShipRejected, match="code=3"):
        await aggregator.ship_to_splunk(
            _CollectorClient({"code": 3, "text": "No data"}), ["{}"]
        )

    # code 0 is the collector accepting the batch.
    await aggregator.ship_to_splunk(
        _CollectorClient({"code": 0, "text": "Success"}), ["{}"]
    )


async def _claimed_batch(r, payload):
    """One enqueued entry, claimed by the consumer, ready to ship."""
    await aggregator.ensure_consumer_group(r)
    await r.xadd(aggregator.QUEUE_KEY, {"payload": payload})
    entries = await r.xreadgroup(
        aggregator.GROUP_NAME,
        aggregator.CONSUMER_NAME,
        {aggregator.QUEUE_KEY: ">"},
        count=1,
    )
    message_id, fields = entries[0][1][0]
    return [(message_id, fields["payload"])]


def _pending_count(pending):
    return pending["pending"] if isinstance(pending, dict) else pending[0]


@pytest.mark.asyncio
async def test_a_rejected_batch_dead_letters_and_never_reads_as_shipped(monkeypatch):
    """AC-12: a refusal reaches the dead letter queue, and the stream entry is
    acked only once its copy is safe there, never one instead of the other."""
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(aggregator, "dump_to_stdout", _noop)
    payload = '{"level":"INFO","message":"rejected by the collector"}'
    batch = await _claimed_batch(r, payload)

    await aggregator.ship_batch(
        _CollectorClient({"code": 3, "text": "No data"}), r, batch
    )

    assert await r.lrange(aggregator.DLQ_KEY, 0, -1) == [payload]
    pending = await r.xpending(aggregator.QUEUE_KEY, aggregator.GROUP_NAME)
    assert _pending_count(pending) == 0  # the DLQ copy is the only copy
    receipts = await r.hgetall(aggregator.SHIP_KEY)
    assert receipts["consecutive_failures"] == "1"
    assert "ShipRejected" in receipts["last_error"]
    assert "code=3" in receipts["last_error"]


@pytest.mark.asyncio
async def test_an_accepted_batch_ships_without_touching_the_dead_letter_queue(monkeypatch):
    """AC-12's other half: code 0 acks the entry, writes no dead letter copy,
    and clears the receipts so an old reason cannot linger."""
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(aggregator, "dump_to_stdout", _noop)
    payload = '{"level":"INFO","message":"accepted by the collector"}'
    await r.hset(aggregator.SHIP_KEY, mapping={"last_error": "stale", "consecutive_failures": "4"})
    batch = await _claimed_batch(r, payload)

    await aggregator.ship_batch(
        _CollectorClient({"code": 0, "text": "Success"}), r, batch
    )

    assert await r.llen(aggregator.DLQ_KEY) == 0
    pending = await r.xpending(aggregator.QUEUE_KEY, aggregator.GROUP_NAME)
    assert _pending_count(pending) == 0
    receipts = await r.hgetall(aggregator.SHIP_KEY)
    assert receipts["consecutive_failures"] == "0"
    assert receipts["last_error"] == ""
