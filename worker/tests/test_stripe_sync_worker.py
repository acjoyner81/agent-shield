"""Stripe usage-sync worker tests: delta token reporting and bookkeeping."""

import os
from unittest.mock import AsyncMock, patch

import pytest

from worker import stripe_sync_worker

USAGE_KEY = "billing:usage:tenant_alpha:2026-09"


@pytest.fixture(autouse=True)
def stripe_key_env():
    os.environ["STRIPE_SECRET_KEY"] = "sk_test_fake"
    yield
    os.environ.pop("STRIPE_SECRET_KEY", None)


@pytest.fixture
def fake_redis_client():
    client = AsyncMock()
    client.scan.return_value = (0, [USAGE_KEY])
    client.hgetall.return_value = {"total_tokens": "250", "reported_tokens": "100"}
    client.get.return_value = "si_xyz"
    return client


@pytest.mark.asyncio
@patch("worker.stripe_sync_worker.redis.from_url")
@patch("worker.stripe_sync_worker._report_usage")
async def test_reports_delta_and_updates_reported_tokens(
    mock_report, mock_redis_from_url, fake_redis_client
):
    """A positive delta is reported to Stripe and reported_tokens is advanced."""
    mock_redis_from_url.return_value = fake_redis_client

    await stripe_sync_worker.sync_usage_to_stripe()

    assert mock_report.call_count == 1
    args, kwargs = mock_report.call_args
    assert args[0] == "si_xyz"
    assert kwargs["quantity"] == 150
    fake_redis_client.hset.assert_called_once_with(USAGE_KEY, "reported_tokens", 250)


@pytest.mark.asyncio
@patch("worker.stripe_sync_worker.redis.from_url")
@patch("worker.stripe_sync_worker._report_usage")
async def test_skips_report_without_subscription_mapping(
    mock_report, mock_redis_from_url, fake_redis_client
):
    """Tenants without a subscription item are skipped, never reported."""
    fake_redis_client.get.return_value = None
    mock_redis_from_url.return_value = fake_redis_client

    await stripe_sync_worker.sync_usage_to_stripe()

    mock_report.assert_not_called()
    fake_redis_client.hset.assert_not_called()


@pytest.mark.asyncio
@patch("worker.stripe_sync_worker.redis.from_url")
@patch("worker.stripe_sync_worker._report_usage")
async def test_no_delta_means_no_report(mock_report, mock_redis_from_url, fake_redis_client):
    """When reported_tokens already matches total, nothing is reported."""
    fake_redis_client.hgetall.return_value = {
        "total_tokens": "250",
        "reported_tokens": "250",
    }
    mock_redis_from_url.return_value = fake_redis_client

    await stripe_sync_worker.sync_usage_to_stripe()

    mock_report.assert_not_called()
    fake_redis_client.hset.assert_not_called()


def test_report_usage_posts_to_usage_records_endpoint():
    """_report_usage POSTs quantity/action/timestamp with a bearer key."""
    with patch("worker.stripe_sync_worker.http_requests.post") as mock_post:
        fake_response = mock_post.return_value
        stripe_sync_worker._report_usage("si_xyz", quantity=150, timestamp=1720000000)

    mock_post.assert_called_once()
    url = mock_post.call_args[0][0]
    assert url == "https://api.stripe.com/v1/subscription_items/si_xyz/usage_records"
    data = mock_post.call_args[1]["data"]
    assert data["quantity"] == 150
    assert data["action"] == "increment"
    assert mock_post.call_args[1]["headers"]["Authorization"].startswith("Bearer ")
    fake_response.raise_for_status.assert_called_once()