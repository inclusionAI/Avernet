"""The new candidate page must not monopolize the shared Backend workers."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from agentclaw.community.adapters.knowledge_platform.catalog import (
    EcbKnowledgeClient,
    RemoteKnowledgeCatalog,
)
from agentclaw.community.adapters.knowledge_platform.config import (
    KnowledgeIntegrationConfig,
)
from agentclaw.community.kernel.knowledge import KnowledgeError
from agentclaw.community.kernel.knowledge_management import KnowledgeManagementError


def test_page_details_resolve_actor_once_and_use_one_acl_checked_batch():
    seen = []
    identities = SimpleNamespace(staff_no=Mock(return_value="actor"))

    def receive(request):
        seen.append(request)
        ids = json.loads(request.content)["kb_ids"]
        return httpx.Response(
            200,
            json={
                "space_id": "1",
                "items": [{"kb_id": kb_id, "space_id": "1"} for kb_id in ids],
            },
        )

    config = KnowledgeIntegrationConfig(True, "https://ecb.test")
    with httpx.Client(transport=httpx.MockTransport(receive)) as client:
        catalog = RemoteKnowledgeCatalog(
            EcbKnowledgeClient(config.catalog_origin, config, client=client), identities
        )
        assert [
            row["kb_id"]
            for row in catalog.details(space_id=1, kb_ids=["a", "b"], actor_id="actor")
        ] == ["a", "b"]
    identities.staff_no.assert_called_once_with("actor")
    assert len(seen) == 1
    assert json.loads(seen[0].content)["actor_staff_no"] == "actor"


def test_page_details_stop_after_budget_expires(monkeypatch):
    from agentclaw.community.adapters.knowledge_platform import catalog as module

    now = [0.0]
    monkeypatch.setattr(module, "monotonic", lambda: now[0])
    calls = []

    def receive(request):
        calls.append(request)
        now[0] += 9
        return httpx.Response(200, json={"kb_id": "a"})

    config = KnowledgeIntegrationConfig(True, "https://ecb.test")
    with httpx.Client(transport=httpx.MockTransport(receive)) as client:
        catalog = RemoteKnowledgeCatalog(
            EcbKnowledgeClient(config.catalog_origin, config, client=client),
            SimpleNamespace(staff_no=lambda _: "actor"),
        )
        with pytest.raises(
            KnowledgeManagementError, match="KNOWLEDGE_DEPENDENCY_UNAVAILABLE"
        ):
            catalog.details(space_id=1, kb_ids=["a", "b"], actor_id="actor")
    assert len(calls) == 1


def test_expired_deadline_does_not_make_http_request(monkeypatch):
    from agentclaw.community.adapters.knowledge_platform import catalog as module

    monkeypatch.setattr(module, "monotonic", lambda: 10)
    config = KnowledgeIntegrationConfig(True, "https://ecb.test")
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail("expired"))
    ) as client:
        remote = EcbKnowledgeClient(config.catalog_origin, config, client=client)
        with pytest.raises(KnowledgeError, match="KNOWLEDGE_DEPENDENCY_UNAVAILABLE"):
            remote.call("GET", "/catalog", deadline=9)
