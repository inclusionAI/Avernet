"""No grant-history prefix scans and no full-list candidate paging."""

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
from agentclaw.community.core.bot_knowledge.repository.models import (
    BotKnowledgeBindingModel,
)
from agentclaw.community.kernel.knowledge import BotKnowledgeScope
from agentclaw.community.kernel.knowledge_management import KnowledgeManagementError

from .conftest import fact


def catalog(client):
    config = KnowledgeIntegrationConfig(True, "https://knowledge.test")
    return RemoteKnowledgeCatalog(
        EcbKnowledgeClient(config.catalog_origin, config, client=client),
        SimpleNamespace(staff_no=lambda v: v),
    )


def batch(items, **changes):
    return {
        "kb_id": "kb",
        "platform": "teamclaw",
        "platform_env": "pre",
        "items": items,
    } | changes


def state(bot="bot", owner="owner", **changes):
    return {"bot_id": bot, "user_id": owner, "granted": False, "revision": 0} | changes


def test_twenty_exact_states_are_one_post_not_history_scan():
    ids = [(f"b{i}", "owner") for i in range(20)]
    calls = []

    def receive(request):
        calls.append(request)
        assert request.method == "POST"
        assert request.url.path == "/api/v1/knowledge/kb/kb/bot-authorization-states"
        assert json.loads(request.content) == {
            "actor_staff_no": "actor",
            "platform": "teamclaw",
            "targets": [{"bot_id": b, "user_id": o} for b, o in ids],
        }
        return httpx.Response(
            200,
            json=batch(
                [state(b, o, revision=9 if i else 0) for i, (b, o) in enumerate(ids)]
            ),
        )

    with httpx.Client(transport=httpx.MockTransport(receive)) as http:
        result = catalog(http).grant_states(
            kb_id="kb", actor_id="actor", identities=ids, env="pre"
        )
    assert len(calls) == 1
    assert [i["revision"] for i in result["items"]] == [0] + [9] * 19


@pytest.mark.parametrize(
    "payload",
    [
        {},
        batch([]),
        batch([state()], platform_env="prod"),
        batch([state()], kb_id="foreign"),
        batch([state()], platform="foreign"),
        batch([state(owner="foreign")]),
        batch([state(), state()]),
        batch([state(revision=True)]),
        batch([state(granted=True)]),
        batch([state(revision=-1)]),
    ],
)
def test_partial_or_wrong_scope_states_never_become_revision_zero(payload):
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as http:
        with pytest.raises(KnowledgeManagementError, match="KNOWLEDGE_GRANTS_INVALID"):
            catalog(http).grant_states(
                kb_id="kb", actor_id="actor", identities=[("bot", "owner")], env="pre"
            )


def test_authorized_page_forwards_bound_and_keeps_opaque_cursor():
    calls = []

    def receive(request):
        calls.append(request)
        assert dict(request.url.params) == {
            "user_id": "owner",
            "cursor": "10",
            "limit": "2",
        }
        return httpx.Response(
            200,
            json={
                "bot_id": "bot",
                "user_id": "owner",
                "space_id": "1",
                "kb_ids": ["a", "b"],
                "next_cursor": 3,
            },
        )

    with httpx.Client(transport=httpx.MockTransport(receive)) as http:
        result = catalog(http).page_authorized(
            scope=BotKnowledgeScope("bot", "owner", "pre"),
            space_id=1,
            cursor=10,
            limit=2,
        )
    assert result == {"kb_ids": ["a", "b"], "next_cursor": 3} and len(calls) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"kb_ids": ["a", "b", "c"]},
        {"kb_ids": ["b", "a"]},
        {"kb_ids": ["a", "a"]},
        {"next_cursor": True},
        {"next_cursor": 10},
        {"next_cursor": -1},
        {"space_id": "other"},
        {"user_id": "other"},
    ],
)
def test_bad_authorized_page_is_rejected(changes):
    payload = {
        "bot_id": "bot",
        "user_id": "owner",
        "space_id": "1",
        "kb_ids": ["a", "b"],
        "next_cursor": None,
    } | changes
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as http:
        with pytest.raises(KnowledgeManagementError, match="KNOWLEDGE_GRANTS_INVALID"):
            catalog(http).page_authorized(
                scope=BotKnowledgeScope("bot", "owner", "pre"),
                space_id=1,
                cursor=10,
                limit=2,
            )


@pytest.mark.parametrize(
    "space_id",
    [None, 0, -1, "1", 1.0, True, 2**63],
)
def test_bad_authorized_page_space_is_rejected_without_provider_io(space_id):
    def refuse(request):
        raise AssertionError("invalid space ids must never reach the provider")

    with httpx.Client(transport=httpx.MockTransport(refuse)) as http:
        with pytest.raises(KnowledgeManagementError, match="INVALID_KNOWLEDGE_INPUT"):
            catalog(http).page_authorized(
                scope=BotKnowledgeScope("bot", "owner", "pre"),
                space_id=space_id,
                cursor=10,
                limit=2,
            )


def test_service_consumes_only_one_page_and_preserves_provider_cursor(world):
    BotKnowledgeBindingModel.__table__.create(world.engine)
    world.catalog.list_authorized = Mock(side_effect=AssertionError("full list"))
    world.catalog.page_authorized = Mock(
        return_value={"kb_ids": ["kb"], "next_cursor": 17}
    )
    world.catalog.details = Mock(return_value=[fact()])
    scope = BotKnowledgeScope("bot", "owner", "pre")
    result = world.service.available_for_bot(
        scope=scope, actor_id="owner", cursor=9, limit=1
    )
    assert result["next_cursor"] == 17 and [
        row["kb_id"] for row in result["items"]
    ] == ["kb"]
    world.catalog.list_authorized.assert_not_called()
    world.catalog.page_authorized.assert_called_once_with(
        scope=scope, space_id=1, cursor=9, limit=1
    )
    world.catalog.details.assert_called_once_with(
        space_id=1, kb_ids=["kb"], actor_id="owner"
    )
