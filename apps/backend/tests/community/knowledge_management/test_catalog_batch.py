"""One ordered, complete batch per detail page; no per-ID fallback."""

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
from agentclaw.community.kernel.knowledge_management import KnowledgeManagementError

from .http_contract import detail


def catalog_for(client, identities):
    config = KnowledgeIntegrationConfig(True, "https://knowledge.test")
    return RemoteKnowledgeCatalog(
        EcbKnowledgeClient(config.catalog_origin, config, client=client), identities
    )


@pytest.mark.parametrize("count", [1, 20])
def test_details_use_one_ordered_batch_with_one_actor_lookup(count):
    ids = [f"kb-{i}" for i in reversed(range(count))]
    rows = [detail(kb=kb) for kb in ids]
    seen = []
    identities = SimpleNamespace(staff_no=Mock(return_value="mapped-actor"))

    def receive(request):
        seen.append(request)
        assert request.method == "POST"
        assert request.url.path == (
            "/api/v1/knowledge/integrations/tc/spaces/1/knowledge-bases/catalog-details"
        )
        assert not request.url.params
        assert json.loads(request.content) == {
            "actor_staff_no": "mapped-actor",
            "kb_ids": ids,
        }
        return httpx.Response(200, json={"space_id": "1", "items": rows})

    with httpx.Client(transport=httpx.MockTransport(receive)) as client:
        assert (
            catalog_for(client, identities).details(
                space_id=1, kb_ids=ids, actor_id="actor"
            )
            == rows
        )
    assert len(seen) == 1
    identities.staff_no.assert_called_once_with("actor")


def test_empty_page_has_no_detail_http_or_identity_lookup():
    identities = SimpleNamespace(staff_no=Mock(side_effect=AssertionError("empty")))
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail("empty"))
    ) as client:
        assert (
            catalog_for(client, identities).details(
                space_id=1, kb_ids=[], actor_id="actor"
            )
            == []
        )
    identities.staff_no.assert_not_called()


@pytest.mark.parametrize("ids", [["a"] * 2, [f"kb-{i}" for i in range(21)], [""]])
def test_invalid_batch_is_rejected_before_lookup_or_http(ids):
    identities = SimpleNamespace(staff_no=Mock(side_effect=AssertionError("invalid")))
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail("invalid"))
    ) as client:
        with pytest.raises(KnowledgeManagementError, match="INVALID_KNOWLEDGE_INPUT"):
            catalog_for(client, identities).details(
                space_id=1, kb_ids=ids, actor_id="actor"
            )
    identities.staff_no.assert_not_called()


@pytest.mark.parametrize(
    "status,code",
    [
        (400, "KNOWLEDGE_REMOTE_INVALID_INPUT"),
        (403, "KNOWLEDGE_REMOTE_ACCESS_DENIED"),
        (404, "KNOWLEDGE_REMOTE_NOT_FOUND"),
        (409, "KNOWLEDGE_REMOTE_CONFLICT"),
        (410, "KNOWLEDGE_REMOTE_RETIRED"),
        (503, "KNOWLEDGE_DEPENDENCY_UNAVAILABLE"),
    ],
)
def test_batch_rejection_never_returns_partial_or_falls_back(status, code):
    seen = []

    def receive(request):
        seen.append(request)
        return httpx.Response(status, json={"detail": {"code": "denied"}})

    with httpx.Client(transport=httpx.MockTransport(receive)) as client:
        catalog = catalog_for(client, SimpleNamespace(staff_no=lambda _: "actor"))
        with pytest.raises(KnowledgeManagementError, match=code):
            catalog.details(space_id=1, kb_ids=["a", "b"], actor_id="actor")
    assert len(seen) == 1
    assert seen[0].method == "POST"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"space_id": "2", "items": [detail(kb="a"), detail(kb="b")]},
        {"space_id": "1", "items": None},
        {"space_id": "1", "items": [detail(kb="a")]},
        {"space_id": "1", "items": [detail(kb="b"), detail(kb="a")]},
        {"space_id": "1", "items": [detail(kb="a"), detail(kb="a")]},
        {"space_id": "1", "items": [detail(kb="a"), detail(kb="b", space="2")]},
        {"space_id": "1", "items": [detail(kb="a"), None]},
        {"space_id": "1", "items": [detail(kb="a"), detail(kb="other")]},
    ],
)
def test_malformed_or_incomplete_batch_fails_closed(payload):
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as client:
        catalog = catalog_for(client, SimpleNamespace(staff_no=lambda _: "actor"))
        with pytest.raises(KnowledgeManagementError, match="KNOWLEDGE_CATALOG_INVALID"):
            catalog.details(space_id=1, kb_ids=["a", "b"], actor_id="actor")


@pytest.mark.parametrize("count", [0, 1, 20])
def test_available_page_has_constant_http_count(world, count):
    from agentclaw.community.core.bot_knowledge.repository.models import (
        BotKnowledgeBindingModel,
    )
    from agentclaw.community.kernel.knowledge import BotKnowledgeScope

    BotKnowledgeBindingModel.__table__.create(world.engine)
    ids = [f"kb-{i:02}" for i in range(count)]
    seen = []

    def receive(request):
        seen.append(request)
        if request.method == "GET":
            assert request.url.path.endswith("/bots/bot/authorized-knowledge-bases")
            return httpx.Response(
                200,
                json={
                    "space_id": "1",
                    "bot_id": "bot",
                    "user_id": "owner",
                    "kb_ids": ids,
                },
            )
        assert request.url.path.endswith("/knowledge-bases/catalog-details")
        assert json.loads(request.content) == {"actor_staff_no": "owner", "kb_ids": ids}
        return httpx.Response(
            200,
            json={
                "space_id": "1",
                "items": [detail(kb=kb) for kb in ids],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(receive)) as client:
        world.service.catalog = catalog_for(
            client, SimpleNamespace(staff_no=lambda value: value)
        )
        result = world.service.available_for_bot(
            scope=BotKnowledgeScope("bot", "owner", "pre"), actor_id="owner", limit=20
        )
    assert [row["kb_id"] for row in result["items"]] == ids
    assert len(seen) == (2 if count else 1)
    assert [request.method for request in seen] == (
        ["GET", "POST"] if count else ["GET"]
    )
