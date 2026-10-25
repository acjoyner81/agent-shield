"""Aggregator DLQ regression tests.

The failure branch once called `r.eval(_DLQ_LUA_SCRIPT, ...)` with names that
did not exist anywhere in the module, so a Splunk outage crashed the aggregator
with NameError instead of dead-lettering the batch. The suite drives the real
`run_aggregator` failure path against fakeredis, not just the helper.
"""
import base64
import json

import fakeredis
import pytest

import gateway.aggregator as aggregator

TEST_HMAC_KEY = bytes(range(32))
TEST_HMAC_KEY_B64 = base64.b64encode(TEST_HMAC_KEY).decode("ascii")


class StopLoop(Exception):
    """Raised out of `run_aggregator` once the scenario has run its course."""


@pytest.fixture(autouse=True)
def configure_hmac_key(monkeypatch):
    monkeypatch.setattr(
        aggregator, "TELEMETRY_USER_ID_HMAC_KEY", TEST_HMAC_KEY_B64
    )


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


@pytest.mark.asyncio
@pytest.mark.parametrize("encoded_key", ["", "not-base64", base64.b64encode(b"short").decode()])
async def test_run_aggregator_refuses_invalid_hmac_keys(monkeypatch, encoded_key):
    monkeypatch.setattr(aggregator, "SPLUNK_HEC_TOKEN", "test-collector-token")
    monkeypatch.setattr(aggregator, "TELEMETRY_USER_ID_HMAC_KEY", encoded_key)

    with pytest.raises(SystemExit):
        await aggregator.run_aggregator()


def test_decode_hmac_key_requires_at_least_32_bytes_and_is_stable():
    assert aggregator.decode_user_id_hmac_key(TEST_HMAC_KEY_B64) == TEST_HMAC_KEY
    assert aggregator.decode_user_id_hmac_key(TEST_HMAC_KEY_B64) == TEST_HMAC_KEY


def test_prepare_payload_pseudonymizes_by_tenant_and_preserves_null_values():
    raw = '{"tenant_id":"tenant_alpha","user_id":"user-123"}'
    other_tenant = '{"tenant_id":"tenant_beta","user_id":"user-123"}'
    absent = '{"tenant_id":"tenant_alpha","message":"no user"}'
    null_user = '{"tenant_id":"tenant_alpha","user_id":null}'
    empty_user = '{"tenant_id":"tenant_alpha","user_id":""}'

    prepared, malformed = aggregator.prepare_payload(raw, TEST_HMAC_KEY)
    prepared_again, _ = aggregator.prepare_payload(raw, TEST_HMAC_KEY)
    prepared_other, _ = aggregator.prepare_payload(other_tenant, TEST_HMAC_KEY)

    assert not malformed
    assert json.loads(prepared)["user_id"] == (
        "hmac-sha256:v1:"
        "530baf2c221c375aa6820a0a38d350fc2155d78bdb51a4009dee7b25783f5836"
    )
    assert json.loads(prepared)["user_id"] == json.loads(prepared_again)["user_id"]
    assert json.loads(prepared)["user_id"] != json.loads(prepared_other)["user_id"]
    assert "user-123" not in prepared
    assert aggregator.prepare_payload(absent, TEST_HMAC_KEY) == (absent, False)
    assert aggregator.prepare_payload(null_user, TEST_HMAC_KEY) == (null_user, False)
    assert aggregator.prepare_payload(empty_user, TEST_HMAC_KEY) == (empty_user, False)


async def _claimed_batch_many(r, payloads):
    await aggregator.ensure_consumer_group(r)
    for payload in payloads:
        await r.xadd(aggregator.QUEUE_KEY, {"payload": payload})
    entries = await r.xreadgroup(
        aggregator.GROUP_NAME,
        aggregator.CONSUMER_NAME,
        {aggregator.QUEUE_KEY: ">"},
        count=len(payloads),
    )
    return [
        (message_id, fields["payload"])
        for _, messages in entries
        for message_id, fields in messages
    ]


@pytest.mark.asyncio
async def test_malformed_user_id_is_dead_lettered_without_blocking_valid_neighbors(monkeypatch):
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    invalid_raw = '{"tenant_id":"tenant_alpha","user_id":7,"message":"bad event"}'
    valid_raw = '{"tenant_id":"tenant_alpha","user_id":"user-123","message":"good event"}'
    batch = await _claimed_batch_many(r, [invalid_raw, valid_raw])
    shipped = []
    stdout_payloads = []

    async def record_ship(client, payloads):
        shipped.extend(payloads)

    async def record_stdout(payloads):
        stdout_payloads.extend(payloads)

    monkeypatch.setattr(aggregator, "ship_to_splunk", record_ship)
    monkeypatch.setattr(aggregator, "dump_to_stdout", record_stdout)

    await aggregator.ship_batch(
        _CollectorClient({"code": 0, "text": "Success"}),
        r,
        batch,
        TEST_HMAC_KEY,
    )

    dead_lettered = json.loads((await r.lrange(aggregator.DLQ_KEY, 0, -1))[0])
    shipped_event = json.loads(shipped[0])
    assert "user_id" not in dead_lettered
    assert dead_lettered["message"] == "bad event"
    assert shipped_event["user_id"].startswith("hmac-sha256:v1:")
    assert "user-123" not in shipped[0]
    assert "user_id" not in stdout_payloads[0]
    assert [fields["payload"] for _, fields in await r.xrange(aggregator.QUEUE_KEY)] == [
        invalid_raw,
        valid_raw,
    ]
    pending = await r.xpending(aggregator.QUEUE_KEY, aggregator.GROUP_NAME)
    assert _pending_count(pending) == 0


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
        _CollectorClient({"code": 3, "text": "No data"}), r, batch, TEST_HMAC_KEY
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
        _CollectorClient({"code": 0, "text": "Success"}), r, batch, TEST_HMAC_KEY
    )

    assert await r.llen(aggregator.DLQ_KEY) == 0
    pending = await r.xpending(aggregator.QUEUE_KEY, aggregator.GROUP_NAME)
    assert _pending_count(pending) == 0
    receipts = await r.hgetall(aggregator.SHIP_KEY)
    assert receipts["consecutive_failures"] == "0"
    assert receipts["last_error"] == ""


async def _stale_pending(r, fields):
    """Enqueue one entry and claim it with a crashed consumer that never acks.

    That leaves the entry pending in the dead consumer's list, which is exactly
    the zombie the XAUTOCLAIM reclaim exists to recover on the next boot.
    """
    await aggregator.ensure_consumer_group(r)
    await r.xadd(aggregator.QUEUE_KEY, fields)
    await r.xreadgroup(
        aggregator.GROUP_NAME,
        "crashed-consumer",
        {aggregator.QUEUE_KEY: ">"},
        count=1,
    )


@pytest.mark.asyncio
async def test_reclaimed_entries_are_shipped_and_acked(monkeypatch):
    """A crash orphan, once reclaimed, is shipped on the next loop and acked,
    and the recovery is counted in the receipts."""
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(aggregator.redis, "from_url", lambda *a, **k: r)
    monkeypatch.setattr(aggregator, "SPLUNK_HEC_TOKEN", "test-collector-token")
    monkeypatch.setattr(aggregator, "MIN_IDLE_TIME_SEC", 0)
    monkeypatch.setattr(aggregator, "dump_to_stdout", _noop)

    async def ca_ready():
        return True

    monkeypatch.setattr(aggregator, "wait_for_ca", ca_ready)

    shipped = []

    async def record_ship(client, payloads):
        shipped.extend(payloads)

    monkeypatch.setattr(aggregator, "ship_to_splunk", record_ship)

    payload = '{"level":"INFO","message":"orphaned by a crash"}'
    await _stale_pending(r, {"payload": payload})

    async def stop_on_first_read(*args, **kwargs):
        raise StopLoop

    monkeypatch.setattr(r, "xreadgroup", stop_on_first_read)

    with pytest.raises(StopLoop):
        await aggregator.run_aggregator()

    assert shipped == [payload]
    pending = await r.xpending(aggregator.QUEUE_KEY, aggregator.GROUP_NAME)
    assert _pending_count(pending) == 0
    assert await r.llen(aggregator.DLQ_KEY) == 0
    receipts = await r.hgetall(aggregator.SHIP_KEY)
    assert receipts["reclaims"] == "1"


@pytest.mark.asyncio
async def test_reclaimed_entries_without_a_payload_dead_letter_and_ack(monkeypatch):
    """A reclaimed entry with no payload cannot be shipped, so it is copied to
    the dead letter queue and acked, breaking the reclaim loop instead of
    pinning an undeliverable entry forever."""
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(aggregator.redis, "from_url", lambda *a, **k: r)
    monkeypatch.setattr(aggregator, "SPLUNK_HEC_TOKEN", "test-collector-token")
    monkeypatch.setattr(aggregator, "MIN_IDLE_TIME_SEC", 0)
    monkeypatch.setattr(aggregator, "dump_to_stdout", _noop)

    async def ca_ready():
        return True

    monkeypatch.setattr(aggregator, "wait_for_ca", ca_ready)

    async def forbid_ship(client, payloads):
        raise AssertionError("a payload-less reclaimed entry must not ship")

    monkeypatch.setattr(aggregator, "ship_to_splunk", forbid_ship)

    await _stale_pending(r, {"message": "no payload field"})

    async def stop_on_first_read(*args, **kwargs):
        raise StopLoop

    monkeypatch.setattr(r, "xreadgroup", stop_on_first_read)

    with pytest.raises(StopLoop):
        await aggregator.run_aggregator()

    assert await r.lrange(aggregator.DLQ_KEY, 0, -1) == ['{"message": "no payload field"}']
    pending = await r.xpending(aggregator.QUEUE_KEY, aggregator.GROUP_NAME)
    assert _pending_count(pending) == 0
    receipts = await r.hgetall(aggregator.SHIP_KEY)
    assert receipts.get("reclaims") is None
