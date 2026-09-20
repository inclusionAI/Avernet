from __future__ import annotations

import httpx
import pytest

from agentclaw.community.adapters.http.tc_file_upload_integrations.tc_resource_ready_publisher import (
    HttpTcResourceReadyPublisher,
)
from agentclaw.community.plugin_api.tc_resource_ready import TcResourceReadyEvent


class _HttpClient:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.calls: list[tuple[str, dict, dict, float]] = []

    def post(self, path, *, json, headers, timeout):
        self.calls.append((path, json, headers, timeout))
        return httpx.Response(
            self.status_code,
            request=httpx.Request("POST", path),
            json={"status": "accepted"},
        )


@pytest.mark.asyncio
async def test_http_publisher_posts_the_exact_event_to_the_contract_path():
    http_client = _HttpClient()
    publisher = HttpTcResourceReadyPublisher(
        base_url="http://knowledge.example/",
        authorization_value="service-secret",
        http_client=http_client,
        timeout_seconds=12.0,
        worker_threads=1,
    )
    try:
        await publisher.publish(TcResourceReadyEvent.for_resource("sr_001"))
    finally:
        publisher._executor.shutdown(wait=True)

    url, payload, headers, timeout = http_client.calls[0]
    assert url == (
        "http://knowledge.example"
        "/api/v1/knowledge/integrations/tc/files/upload-completed"
    )
    assert payload == {
        "schema_version": "1",
        "event_id": "tc.resource.ready:sr_001",
        "res_id": "sr_001",
    }
    assert headers == {"Authorization": "Bearer service-secret"}
    assert timeout == 12.0


@pytest.mark.asyncio
async def test_http_publisher_rejects_an_unconfigured_base_url():
    publisher = HttpTcResourceReadyPublisher(
        base_url="",
        authorization_value="service-secret",
        http_client=_HttpClient(),
        worker_threads=1,
    )
    try:
        with pytest.raises(ValueError, match="ecb_base_url_not_configured"):
            await publisher.publish(TcResourceReadyEvent.for_resource("sr_001"))
    finally:
        publisher._executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_http_publisher_rejects_an_unconfigured_service_token():
    publisher = HttpTcResourceReadyPublisher(
        base_url="http://knowledge.example",
        authorization_value="",
        http_client=_HttpClient(),
        worker_threads=1,
    )
    try:
        with pytest.raises(ValueError, match="tc_file_service_token_not_configured"):
            await publisher.publish(TcResourceReadyEvent.for_resource("sr_001"))
    finally:
        publisher._executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_http_publisher_surfaces_non_2xx_response():
    publisher = HttpTcResourceReadyPublisher(
        base_url="http://knowledge.example",
        authorization_value="service-secret",
        http_client=_HttpClient(status_code=502),
        worker_threads=1,
    )
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await publisher.publish(TcResourceReadyEvent.for_resource("sr_001"))
    finally:
        publisher._executor.shutdown(wait=True)


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"timeout_seconds": 0}, "timeout_seconds_must_be_positive"),
        ({"worker_threads": 0}, "worker_threads_must_be_positive"),
    ],
)
def test_http_publisher_rejects_unbounded_execution_configuration(kwargs, error):
    with pytest.raises(ValueError, match=error):
        HttpTcResourceReadyPublisher(
            base_url="http://knowledge.example",
            authorization_value="service-secret",
            http_client=_HttpClient(),
            **kwargs,
        )
