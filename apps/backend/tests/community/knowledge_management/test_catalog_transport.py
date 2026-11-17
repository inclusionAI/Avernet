"""Catalog requests need neither credentials nor a reverse Bot identity query."""

import httpx
import pytest
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


@pytest.mark.parametrize(
    ("profile", "origin", "accepted"),
    [
        ("local-k8s", "http://ecb:31236", True),
        ("local-k8s", "http://ecb.simulator.svc.cluster.local:31236", True),
        ("local-k8s", "http://127.0.0.1:31236", False),
        ("community", "http://ecb:31236", False),
    ],
)
def test_local_k8s_allows_only_the_simulator_ecb_http_origin(profile, origin, accepted):
    from agentclaw.community.di import DeployProfile
    from agentclaw.community.di.modules.knowledge_management_module import (
        KnowledgeManagementModule,
    )

    module = KnowledgeManagementModule(DeployProfile(profile))
    config = KnowledgeIntegrationConfig(enabled=True, catalog_origin=origin)
    if accepted:
        client = module.remote(config, object())
        assert client.origin == origin
    else:
        with pytest.raises(ValueError, match="trusted HTTPS origin"):
            module.remote(config, object())


def test_ecb_catalog_preserves_yuque_credential_precondition():
    from agentclaw.community.kernel.knowledge import KnowledgeError

    config = KnowledgeIntegrationConfig(enabled=True, catalog_origin="https://ecb.test")
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                412,
                json={"detail": "YUQUE_CREDENTIAL_MISSING"},
            )
        )
    ) as http:
        client = EcbKnowledgeClient(config.catalog_origin, config, client=http)
        with pytest.raises(KnowledgeError) as error:
            client.call("GET", "/api/v1/knowledge/data-source/connectors/yuque/books")
    assert error.value.code == "KNOWLEDGE_REMOTE_REJECTED"
    assert error.value.status == 412
