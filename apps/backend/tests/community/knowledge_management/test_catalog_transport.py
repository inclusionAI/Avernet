"""Catalog requests need neither credentials nor a reverse Bot identity query."""

import httpx
from agentclaw.community.adapters.knowledge_platform.catalog import EcbKnowledgeClient
from agentclaw.community.adapters.knowledge_platform.config import (
    KnowledgeIntegrationConfig,
)


def test_catalog_request_works_without_service_credentials():
    requests = []

    def receive(request):
        requests.append(request)
        return httpx.Response(200, json={"items": [], "next_cursor": None})

    config = KnowledgeIntegrationConfig(enabled=True, catalog_origin="https://ecb.test")
    with httpx.Client(transport=httpx.MockTransport(receive)) as http:
        client = EcbKnowledgeClient(config.catalog_origin, config, client=http)
        assert client.call("GET", "/catalog") == {"items": [], "next_cursor": None}
    assert len(requests) == 1
    assert "authorization" not in requests[0].headers


def test_disabled_integration_does_not_contact_ecb():
    import pytest
    from agentclaw.community.kernel.knowledge import KnowledgeError

    config = KnowledgeIntegrationConfig(
        enabled=False, catalog_origin="https://ecb.test"
    )
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail("must stay local"))
    ) as http:
        client = EcbKnowledgeClient(config.catalog_origin, config, client=http)
        with pytest.raises(KnowledgeError, match="KNOWLEDGE_INTEGRATION_UNAVAILABLE"):
            client.call("GET", "/catalog")


def test_ecb_failure_is_not_treated_as_an_empty_catalog():
    import pytest
    from agentclaw.community.kernel.knowledge import KnowledgeError

    config = KnowledgeIntegrationConfig(enabled=True, catalog_origin="https://ecb.test")
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(503))
    ) as http:
        client = EcbKnowledgeClient(config.catalog_origin, config, client=http)
        with pytest.raises(KnowledgeError, match="KNOWLEDGE_DEPENDENCY_UNAVAILABLE"):
            client.call("GET", "/catalog")
