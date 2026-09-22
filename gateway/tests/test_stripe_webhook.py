import json
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from gateway.main import app

client = TestClient(app)


@pytest.fixture
def mock_stripe_event():
    return {
        "id": "evt_test",
        "type": "customer.subscription.created",
        "data": {
            "object": {
                "customer": "cus_123456",
                "status": "active",
                "metadata": {"tenant_id": "tenant_test_1"},
                "items": {
                    "data": [
                        {"price": {"product": "prod_VHXVVqTBAo3vfR"}}
                    ]
                }
            }
        }
    }


class TestWebhooksStripeEndpoint:
    @patch("stripe.Webhook.construct_event")
    @patch("gateway.entitlements.redis_client.setex")
    def test_stripe_webhook_subscription_created(self, mock_setex, mock_construct_event, mock_stripe_event):
        mock_construct_event.return_value = mock_stripe_event

        response = client.post(
            "/v1/webhooks/stripe",
            data=json.dumps(mock_stripe_event),
            headers={"stripe-signature": "test_sig"}
        )

        assert response.status_code == 200
        assert response.json() == {"status": "success"}
        mock_setex.assert_called_once()

    @patch("stripe.Webhook.construct_event")
    def test_stripe_webhook_invalid_signature(self, mock_construct_event):
        import stripe
        mock_construct_event.side_effect = stripe.error.SignatureVerificationError("Invalid", "sig")

        response = client.post(
            "/v1/webhooks/stripe",
            data="{}",
            headers={"stripe-signature": "invalid_sig"}
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid signature"

    @patch("stripe.Webhook.construct_event")
    def test_stripe_webhook_subscription_deleted(self, mock_construct_event):
        deleted_event = {
            "id": "evt_deleted",
            "type": "customer.subscription.deleted",
            "data": {
                "object": {
                    "customer": "cus_123456",
                    "status": "canceled",
                    "metadata": {"tenant_id": "tenant_test_1"},
                    "items": {"data": []},
                }
            },
        }
        mock_construct_event.return_value = deleted_event

        with patch("gateway.webhooks.clear_tenant_entitlements") as mock_clear:
            response = client.post(
                "/v1/webhooks/stripe",
                data=json.dumps(deleted_event),
                headers={"stripe-signature": "test_sig"}
            )

        assert response.status_code == 200
        assert response.json()["status"] == "success"
        mock_clear.assert_called_once_with("tenant_test_1")

    @patch("stripe.Webhook.construct_event")
    def test_stripe_webhook_subscription_updated(self, mock_construct_event):
        updated_event = {
            "id": "evt_updated",
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "customer": "cus_123456",
                    "status": "past_due",
                    "metadata": {"tenant_id": "tenant_test_1"},
                    "items": {
                        "data": [
                            {"price": {"product": "prod_VHXVVqTBAo3vfR"}}
                        ]
                    }
                }
            },
        }
        mock_construct_event.return_value = updated_event

        with patch("gateway.webhooks.set_tenant_entitlements") as mock_set:
            response = client.post(
                "/v1/webhooks/stripe",
                data=json.dumps(updated_event),
                headers={"stripe-signature": "test_sig"}
            )

        assert response.status_code == 200
        mock_set.assert_called_once()
        call_args = mock_set.call_args
        assert call_args[0][2] == "past_due"

    @patch("stripe.Webhook.construct_event")
    def test_stripe_webhook_unexpected_event_type(self, mock_construct_event):
        unexpected_event = {
            "id": "evt_unexpected",
            "type": "charge.succeeded",
            "data": {"object": {}},
        }
        mock_construct_event.return_value = unexpected_event

        response = client.post(
            "/v1/webhooks/stripe",
            data=json.dumps(unexpected_event),
            headers={"stripe-signature": "test_sig"}
        )

        assert response.status_code == 200
        assert response.json()["status"] == "success"

    @patch("stripe.Webhook.construct_event")
    def test_stripe_webhook_signature_verification_forged(self, mock_construct_event):
        import stripe
        mock_construct_event.side_effect = stripe.error.SignatureVerificationError("Forged", "sig")

        response = client.post(
            "/v1/webhooks/stripe",
            data=b'{"fake": "data"}',
            headers={"stripe-signature": "forged_sig"}
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid signature"

    @patch("stripe.Webhook.construct_event")
    def test_stripe_webhook_missing_signature(self, mock_construct_event):
        import stripe
        mock_construct_event.side_effect = stripe.error.SignatureVerificationError("Missing", "sig")

        response = client.post(
            "/v1/webhooks/stripe",
            data="{}",
            headers={}
        )

        assert response.status_code == 400


class TestBillingWebhookEndpoint:
    """Tests for the /api/v1/billing/webhook route in gateway/webhooks.py."""

    @patch("stripe.Webhook.construct_event")
    @patch("gateway.entitlements.redis_client.setex")
    def test_billing_webhook_subscription_created(self, mock_setex, mock_construct_event):
        mock_stripe_event = {
            "id": "evt_billing",
            "type": "customer.subscription.created",
            "data": {
                "object": {
                    "customer": "cus_billing_test",
                    "status": "active",
                    "metadata": {"tenant_id": "tenant_billing"},
                    "items": {"data": [{"price": {"product": "prod_VHXVVqTBAo3vfR"}}]},
                }
            },
        }
        mock_construct_event.return_value = mock_stripe_event

        response = client.post(
            "/api/v1/billing/webhook",
            data=json.dumps(mock_stripe_event),
            headers={"stripe-signature": "test_sig"}
        )

        assert response.status_code == 200
        assert response.json() == {"status": "success"}
        mock_setex.assert_called_once()

    @patch("stripe.Webhook.construct_event")
    def test_billing_webhook_invalid_signature(self, mock_construct_event):
        import stripe
        mock_construct_event.side_effect = stripe.error.SignatureVerificationError("Invalid", "sig")

        response = client.post(
            "/api/v1/billing/webhook",
            data=b"{}",
            headers={"stripe-signature": "invalid_sig"}
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid signature"

    @patch("stripe.Webhook.construct_event")
    def test_billing_webhook_subscription_deleted(self, mock_construct_event):
        deleted_event = {
            "id": "evt_del_billing",
            "type": "customer.subscription.deleted",
            "data": {
                "object": {
                    "customer": "cus_billing",
                    "status": "canceled",
                    "metadata": {"tenant_id": "tenant_billing"},
                    "items": {"data": []},
                }
            },
        }
        mock_construct_event.return_value = deleted_event

        with patch("gateway.webhooks.clear_tenant_entitlements") as mock_clear:
            response = client.post(
                "/api/v1/billing/webhook",
                data=json.dumps(deleted_event),
                headers={"stripe-signature": "test_sig"}
            )

        assert response.status_code == 200
        mock_clear.assert_called_once()

    @patch("stripe.Webhook.construct_event")
    def test_billing_webhook_past_due_downgrades(self, mock_construct_event):
        updated_event = {
            "id": "evt_past_due",
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "customer": "cus_billing",
                    "status": "past_due",
                    "metadata": {"tenant_id": "tenant_billing"},
                    "items": {"data": [{"price": {"product": "prod_VHXVVqTBAo3vfR"}}]},
                }
            },
        }
        mock_construct_event.return_value = updated_event

        with patch("gateway.webhooks.set_tenant_entitlements") as mock_set:
            response = client.post(
                "/api/v1/billing/webhook",
                data=json.dumps(updated_event),
                headers={"stripe-signature": "test_sig"}
            )

        assert response.status_code == 200
        mock_set.assert_called_once()
        call_args = mock_set.call_args
        assert call_args[0][2] == "past_due"


class TestWebhookSecurityCases:
    def test_webhook_no_signature_header_rejected(self):
        import stripe
        with patch("stripe.Webhook.construct_event") as mock_construct:
            mock_construct.side_effect = stripe.error.SignatureVerificationError("Missing", "sig")
            response = client.post(
                "/v1/webhooks/stripe",
                data=b"{}",
                headers={}
            )
        assert response.status_code == 400

    def test_webhook_empty_body(self):
        response = client.post(
            "/v1/webhooks/stripe",
            data=b"",
            headers={"stripe-signature": "test_sig"}
        )
        assert response.status_code in (400, 200)

    def test_webhook_returns_success_for_charge_event(self):
        charge_event = {
            "id": "evt_charge",
            "type": "charge.succeeded",
            "data": {"object": {}},
        }
        with patch("stripe.Webhook.construct_event", return_value=charge_event):
            response = client.post(
                "/v1/webhooks/stripe",
                data=json.dumps(charge_event),
                headers={"stripe-signature": "test_sig"}
            )
        assert response.status_code == 200
        assert response.json()["status"] == "success"