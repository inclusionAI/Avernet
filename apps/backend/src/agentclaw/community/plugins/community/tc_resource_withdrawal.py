"""Authenticated ECB withdrawal adapter with strict durable-ack validation."""

from agentclaw.community.plugin_api.http_client import (
    HttpClient,
    HttpClientRequestError,
    HttpClientTimeoutError,
)
from agentclaw.community.plugin_api.impl_registry import Mode, plugin_impl
from agentclaw.community.plugin_api.tc_resource_withdrawal import (
    ResourceWithdrawalEvent,
    ResourceWithdrawalPublisherPlugin,
    WithdrawalDeliveryError,
    WithdrawalReceipt,
)

_WITHDRAWAL_PATH = "/api/v1/knowledge/integrations/tc/files/withdraw-by-resource"


@plugin_impl(mode=Mode.PROD, rationale="authenticated durable ECB withdrawal receipt")
class HttpResourceWithdrawalPublisher(ResourceWithdrawalPublisherPlugin):
    def __init__(
        self,
        *,
        base_url: str,
        authorization_value: str,
        http_client: HttpClient,
        timeout_seconds: float,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._authorization = authorization_value
        self._client = http_client
        self._timeout = timeout_seconds

    def publish(self, event: ResourceWithdrawalEvent) -> WithdrawalReceipt:
        if not self._base_url or not self._authorization.strip():
            raise WithdrawalDeliveryError("missing_delivery_config", retryable=False)
        try:
            response = self._client.post(
                self._base_url + _WITHDRAWAL_PATH,
                json=event.as_payload(),
                headers={"Authorization": f"Bearer {self._authorization}"},
                timeout=self._timeout,
            )
        except HttpClientTimeoutError:
            raise WithdrawalDeliveryError("timeout", retryable=True) from None
        except HttpClientRequestError:
            raise WithdrawalDeliveryError("network_error", retryable=True) from None
        if not 200 <= response.status_code < 300:
            code = response.status_code
            raise WithdrawalDeliveryError(
                f"http_{code}", retryable=code in {408, 429} or code >= 500
            )
        try:
            receipt = response.json()
        except ValueError:
            raise WithdrawalDeliveryError("invalid_receipt", retryable=False) from None
        if (
            not isinstance(receipt, dict)
            or receipt.get("accepted") is not True
            or receipt.get("event_id") != event.event_id
            or receipt.get("status") not in ("pending", "applied")
        ):
            raise WithdrawalDeliveryError("invalid_receipt", retryable=False)
        return WithdrawalReceipt(event_id=event.event_id, status=receipt["status"])
