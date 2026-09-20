import json
import pytest
from unittest.mock import patch
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

@patch("stripe.Webhook.construct_event")
@patch("gateway.entitlements.redis_client.setex")
def test_stripe_webhook_subscription_created(mock_setex, mock_construct_event, mock_stripe_event):
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
def test_stripe_webhook_invalid_signature(mock_construct_event):
    import stripe
    mock_construct_event.side_effect = stripe.error.SignatureVerificationError("Invalid", "sig")
    
    response = client.post(
        "/v1/webhooks/stripe",
        data="{}",
        headers={"stripe-signature": "invalid_sig"}
    )
    
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid signature"