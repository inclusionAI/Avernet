"""HTTP adapter's wire contract and fail-closed classification."""

from unittest.mock import Mock

import httpx
import pytest

from agentclaw.community.plugin_api.tc_resource_withdrawal import (
    ResourceWithdrawalEvent,
    WithdrawalDeliveryError,
)
from agentclaw.community.plugins.community.tc_resource_withdrawal import (
    HttpResourceWithdrawalPublisher,
)

EVENT = ResourceWithdrawalEvent.for_resource("sr_test")


def publisher(response):
    client = Mock()
    client.post.return_value = response
    return HttpResourceWithdrawalPublisher(
        base_url="https://ecb.example.test",
        authorization_value="test-only",
        http_client=client,
        timeout_seconds=3,
    ), client


@pytest.mark.parametrize("status", ["pending", "applied"])
def test_exact_payload_authenticated_durable_ack(status):
    adapter, client = publisher(
        httpx.Response(
            202, json={"event_id": EVENT.event_id, "accepted": True, "status": status}
        )
    )
    assert adapter.publish(EVENT).event_id == EVENT.event_id
    client.post.assert_called_once_with(
        "https://ecb.example.test/api/v1/knowledge/integrations/tc/files/withdraw-by-resource",
        json={"event_id": EVENT.event_id, "res_id": "sr_test"},
        headers={"Authorization": "Bearer test-only"},
        timeout=3,
    )


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"event_id": "other", "accepted": True, "status": "pending"},
        {"event_id": EVENT.event_id, "accepted": 1, "status": "pending"},
        {"event_id": EVENT.event_id, "accepted": False, "status": "pending"},
        {"event_id": EVENT.event_id, "accepted": True, "status": "unknown"},
        [],
        "html",
    ],
)
def test_invalid_2xx_is_permanent_failure(body):
    adapter, _ = publisher(httpx.Response(200, json=body))
    with pytest.raises(WithdrawalDeliveryError) as exc:
        adapter.publish(EVENT)
    assert not exc.value.retryable
    assert str(exc.value) == "invalid_receipt"


@pytest.mark.parametrize(
    "code,retryable",
    [
        (301, False),
        (400, False),
        (401, False),
        (403, False),
        (404, False),
        (409, False),
        (408, True),
        (429, True),
        (500, True),
        (503, True),
    ],
)
def test_http_classification_does_not_log_response_body(code, retryable):
    adapter, _ = publisher(
        httpx.Response(code, text="sensitive response must not escape")
    )
    with pytest.raises(WithdrawalDeliveryError) as exc:
        adapter.publish(EVENT)
    assert exc.value.retryable == retryable
    assert str(exc.value) == f"http_{code}"


@pytest.mark.parametrize(
    "error", [httpx.ReadTimeout("sensitive"), httpx.ConnectError("sensitive")]
)
def test_network_errors_retry_without_leaking_transport_details(error):
    adapter, client = publisher(None)
    client.post.side_effect = error
    with pytest.raises(WithdrawalDeliveryError) as exc:
        adapter.publish(EVENT)
    assert exc.value.retryable
    assert "sensitive" not in str(exc.value)


@pytest.mark.parametrize(
    "response", [httpx.Response(204), httpx.Response(200, text="<html>private</html>")]
)
@pytest.mark.parametrize("configured", [False, True])
def test_missing_config_or_non_json_cannot_ack(response, configured):
    adapter, client = publisher(response)
    if not configured:
        adapter._authorization = ""
    with pytest.raises(WithdrawalDeliveryError) as exc:
        adapter.publish(EVENT)
    assert str(exc.value) == (
        "invalid_receipt" if configured else "missing_delivery_config"
    )
    if not configured:
        client.post.assert_not_called()
