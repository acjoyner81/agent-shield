from unittest.mock import patch, MagicMock
import json
import os

import pytest
import stripe
from fastapi.testclient import TestClient

from gateway.main import app
from gateway.billing import cache_entitlement, get_cached_entitlement, ensure_stripe_customer, ENTITLEMENT_KEY, ENTITLEMENT_TTL

client = TestClient(app)


@pytest.fixture(autouse=True)
def enable_dev_mode():
    os.environ["DEV_MODE"] = "true"
    yield
    os.environ.pop("DEV_MODE", None)

CLIENT_HEADERS = {"Authorization": "Bearer dev-mock-token"}
WEBHOOK_HEADERS = {"stripe-signature": "test_sig"}

CHECKOUT_EVENT = {
    "id": "evt_checkout",
    "type": "checkout.session.completed",
    "data": {"object": {"customer": "cus_123", "metadata": {"tenant_id": "tenant_alpha"}}},
}

SUBSCRIPTION_EVENT = {
    "id": "evt_sub_created",
    "type": "customer.subscription.created",
    "data": {
        "object": {
            "customer": "cus_123",
            "status": "active",
            "metadata": {"tenant_id": "tenant_alpha"},
            "items": {
                "data": [
                    {"price": {"product": "prod_VHXVVqTBAo3vfR"}}
                ]
            },
        }
    },
}

DELETED_EVENT = {
    "id": "evt_sub_deleted",
    "type": "customer.subscription.deleted",
    "data": {
        "object": {
            "customer": "cus_123",
            "status": "canceled",
            "metadata": {"tenant_id": "tenant_alpha"},
            "items": {"data": []},
        }
    },
}

SUBSCRIPTION_UPDATED_EVENT = {
    "id": "evt_sub_updated",
    "type": "customer.subscription.updated",
    "data": {
        "object": {
            "customer": "cus_123",
            "status": "past_due",
            "metadata": {"tenant_id": "tenant_alpha"},
            "items": {
                "data": [
                    {"price": {"product": "prod_VHXVVqTBAo3vfR"}}
                ]
            },
        }
    },
}


@patch("gateway.billing.stripe.checkout.Session.create")
@patch("gateway.billing.ensure_stripe_customer")
def test_checkout_creates_session(mock_customer, mock_session_create):
    mock_customer.return_value = "cus_123"
    mock_session_create.return_value = type("S", (), {"url": "https://checkout.stripe.com/123", "id": "cs_test"})

    response = client.post(
        "/v1/billing/checkout",
        params={"price_id": "price_test"},
        headers=CLIENT_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["checkout_url"] == "https://checkout.stripe.com/123"
    assert body["session_id"] == "cs_test"


def test_checkout_missing_price_id():
    response = client.post("/v1/billing/checkout", params={}, headers=CLIENT_HEADERS)
    assert response.status_code == 400
    assert response.json()["detail"] == "price_id is required"


@patch("gateway.billing.stripe.billing_portal.Session.create")
def test_portal_no_customer_returns_404(mock_portal):
    response = client.post("/v1/billing/portal", headers=CLIENT_HEADERS)
    assert response.status_code == 404
    assert "No Stripe customer" in response.json()["detail"]


@patch("gateway.billing.redis.Redis.get")
@patch("gateway.billing.stripe.billing_portal.Session.create")
def test_portal_returns_url(mock_portal, mock_redis_get):
    mock_redis_get.return_value = "cus_123"
    mock_portal.return_value = type("P", (), {"url": "https://billing.stripe.com/portals/123"})

    response = client.post("/v1/billing/portal", headers=CLIENT_HEADERS)

    assert response.status_code == 200
    assert response.json()["portal_url"] == "https://billing.stripe.com/portals/123"


@patch("gateway.billing.stripe.Webhook.construct_event")
@patch("gateway.billing.r.setex")
def test_webhook_subscription_created_caches_entitlement(mock_setex, mock_construct_event):
    mock_construct_event.return_value = SUBSCRIPTION_EVENT

    response = client.post("/v1/billing/webhook", data=json.dumps(SUBSCRIPTION_EVENT), headers=WEBHOOK_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"status": "success"}
    mock_setex.assert_called_once()


@patch("gateway.billing.stripe.Webhook.construct_event")
@patch("gateway.billing.r.delete")
def test_webhook_subscription_deleted_clears_entitlement(mock_delete, mock_construct_event):
    mock_construct_event.return_value = DELETED_EVENT

    response = client.post("/v1/billing/webhook", data=json.dumps(DELETED_EVENT), headers=WEBHOOK_HEADERS)

    assert response.status_code == 200
    assert response.json() == {"status": "success"}
    mock_delete.assert_called_once()


@patch("gateway.billing.stripe.Webhook.construct_event")
def test_webhook_invalid_signature_rejected(mock_construct_event):
    mock_construct_event.side_effect = stripe.error.SignatureVerificationError("Invalid", "sig")

    response = client.post("/v1/billing/webhook", data=b"{}", headers=WEBHOOK_HEADERS)

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid signature"


class TestCacheEntitlement:
    def test_cache_entitlement_writes_redis(self):
        mock_redis = MagicMock()
        with patch("gateway.billing.r", mock_redis):
            data = {"status": "active", "tier": "pro"}
            cache_entitlement("tenant_alpha", data)
            key = ENTITLEMENT_KEY.format(tenant_id="tenant_alpha")
            mock_redis.setex.assert_called_once()
            call_args = mock_redis.setex.call_args
            assert call_args[0][0] == key
            assert call_args[0][1] == ENTITLEMENT_TTL
            assert "updated_at" in json.loads(call_args[0][2])

    def test_cache_entitlement_sets_updated_at(self):
        mock_redis = MagicMock()
        with patch("gateway.billing.r", mock_redis):
            data = {"status": "active", "tier": "starter"}
            cache_entitlement("tenant_beta", data)
            stored_data = json.loads(mock_redis.setex.call_args[0][2])
            assert "updated_at" in stored_data
            assert stored_data["status"] == "active"
            assert stored_data["tier"] == "starter"


class TestGetCachedEntitlement:
    def test_get_cached_entitlement_returns_data(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = json.dumps({"status": "active", "tier": "pro"})
        with patch("gateway.billing.r", mock_redis):
            result = get_cached_entitlement("tenant_alpha")
            assert result is not None
            assert result["status"] == "active"
            assert result["tier"] == "pro"

    def test_get_cached_entitlement_returns_none(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        with patch("gateway.billing.r", mock_redis):
            result = get_cached_entitlement("tenant_unknown")
            assert result is None

    def test_get_cached_entitlement_empty_string(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = ""
        with patch("gateway.billing.r", mock_redis):
            result = get_cached_entitlement("tenant_alpha")
            assert result is None


class TestEnsureStripeCustomer:
    def test_ensure_stripe_customer_returns_existing(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = "cus_existing"
        with patch("gateway.billing.r", mock_redis):
            with patch("gateway.billing.stripe.Customer.create") as mock_create:
                result = ensure_stripe_customer("tenant_alpha")
                assert result == "cus_existing"
                mock_create.assert_not_called()

    def test_ensure_stripe_customer_creates_new(self):
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_customer = MagicMock()
        mock_customer.id = "cus_new"
        with patch("gateway.billing.r", mock_redis):
            with patch("gateway.billing.stripe.Customer.create", return_value=mock_customer) as mock_create:
                result = ensure_stripe_customer("tenant_alpha", email="test@example.com")
                assert result == "cus_new"
                mock_create.assert_called_once()
                assert mock_create.call_args[1]["metadata"]["tenant_id"] == "tenant_alpha"
                assert mock_create.call_args[1]["email"] == "test@example.com"


class TestCheckoutEdgeCases:
    def test_checkout_stripe_error_returns_400(self):
        mock_customer = MagicMock(return_value="cus_123")
        with patch("gateway.billing.ensure_stripe_customer", mock_customer), \
             patch("gateway.billing.stripe.checkout.Session.create") as mock_session:
            mock_session.side_effect = stripe.error.StripeError("API Error", "user_message")
            response = client.post(
                "/v1/billing/checkout",
                params={"price_id": "bad_price"},
                headers=CLIENT_HEADERS,
            )
        assert response.status_code == 400
        assert "Stripe error" in response.json()["detail"]

    def test_checkout_with_empty_price_id(self):
        response = client.post(
            "/v1/billing/checkout",
            params={"price_id": ""},
            headers=CLIENT_HEADERS,
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "price_id is required"


class TestWebhookSecurityCases:
    def test_webhook_missing_signature_header(self):
        with patch("gateway.billing.stripe.Webhook.construct_event") as mock_construct:
            mock_construct.side_effect = stripe.error.SignatureVerificationError("Missing", "sig")
            response = client.post("/v1/billing/webhook", data=b"{}", headers={})
        assert response.status_code == 400

    def test_webhook_empty_payload(self):
        with patch("gateway.billing.stripe.Webhook.construct_event") as mock_construct:
            mock_construct.side_effect = stripe.error.SignatureVerificationError("Empty", "sig")
            response = client.post(
                "/v1/billing/webhook",
                data=b"",
                headers=WEBHOOK_HEADERS,
            )
        assert response.status_code == 400

    def test_webhook_no_metadata_tenant_falls_back_to_customer(self):
        event_no_tenant = {
            "id": "evt_no_tenant",
            "type": "customer.subscription.created",
            "data": {
                "object": {
                    "customer": "cus_no_tenant_id",
                    "status": "active",
                    "metadata": {},
                    "items": {"data": []},
                }
            },
        }
        with patch("gateway.billing.stripe.Webhook.construct_event", return_value=event_no_tenant), \
             patch("gateway.billing.r.setex"), \
             patch("gateway.billing.set_tenant_entitlements"):
            response = client.post(
                "/v1/billing/webhook",
                data=json.dumps(event_no_tenant),
                headers=WEBHOOK_HEADERS,
            )
        assert response.status_code == 200

    def test_webhook_past_due_downgrades_to_free(self):
        with patch("gateway.billing.stripe.Webhook.construct_event", return_value=SUBSCRIPTION_UPDATED_EVENT), \
             patch("gateway.billing.r.setex"), \
             patch("gateway.billing.set_tenant_entitlements") as mock_set_ent:
            response = client.post(
                "/v1/billing/webhook",
                data=json.dumps(SUBSCRIPTION_UPDATED_EVENT),
                headers=WEBHOOK_HEADERS,
            )
        assert response.status_code == 200
        assert mock_set_ent.called
        call_args = mock_set_ent.call_args
        assert call_args[0][1] == "free"
        assert call_args[0][2] == "past_due"

    def test_webhook_subscription_deleted_no_entitlement_data(self):
        event = {
            "id": "evt_del",
            "type": "customer.subscription.deleted",
            "data": {
                "object": {
                    "customer": "cus_123",
                    "metadata": {"tenant_id": "tenant_alpha"},
                    "items": {"data": []},
                }
            },
        }
        with patch("gateway.billing.stripe.Webhook.construct_event", return_value=event), \
             patch("gateway.billing.r.delete") as mock_delete, \
             patch("gateway.billing.clear_tenant_entitlements"):
            response = client.post(
                "/v1/billing/webhook",
                data=json.dumps(event),
                headers=WEBHOOK_HEADERS,
            )
        assert response.status_code == 200
        mock_delete.assert_called_once()


class TestWebhookPaymentSecurity:
    def test_webhook_unexpected_event_type_returns_success(self):
        unexpected_event = {
            "id": "evt_unexpected",
            "type": "charge.succeeded",
            "data": {"object": {}},
        }
        with patch("gateway.billing.stripe.Webhook.construct_event", return_value=unexpected_event):
            response = client.post(
                "/v1/billing/webhook",
                data=json.dumps(unexpected_event),
                headers=WEBHOOK_HEADERS,
            )
        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_webhook_signature_verification_rejects_forged(self):
        with patch("gateway.billing.stripe.Webhook.construct_event") as mock_construct:
            mock_construct.side_effect = stripe.error.SignatureVerificationError("Forged payload", "sig")
            response = client.post(
                "/v1/billing/webhook",
                data=b'{"fake": "data"}',
                headers={"stripe-signature": "forged_sig"},
            )
        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid signature"