"""First-use catalog and authoritative grant-state regression contracts."""

from types import SimpleNamespace

import httpx
import pytest
from agentclaw.community.plugin_api.space_knowledge import SpaceKnowledgeError

from .conftest import make_catalog

PREFIX = "/api/knowledge/spaces/1/knowledge-bases"


def test_never_activated_is_empty_without_remote_or_sync_writes(client, world):
    calls = []
    world.service.sync = SimpleNamespace(get_context=lambda **kw: calls.append(kw))
    result = client.get(PREFIX)
    assert result.status_code == 200
    assert result.json() == {"items": [], "knowledge_bases": [], "next_cursor": None}
    assert calls == [{"space_id": 1, "environment": "pre"}]
    assert world.catalog.calls == []


def test_pending_sync_fails_closed_before_remote(client, world):
    def pending(**kw):
        raise SpaceKnowledgeError("SPACE_NOT_SYNCED")

    world.service.sync = SimpleNamespace(get_context=pending)
    result = client.get(PREFIX)
    assert result.status_code == 503
    assert result.json()["detail"]["code"] == "SPACE_NOT_SYNCED"
    assert world.catalog.calls == []


def test_authorization_batch_keeps_revoked_revision_and_composite_identity(
    client, world
):
    calls = []

    def states(**kw):
        calls.append(kw)
        return {
            "items": [
                {"bot_id": "bot", "owner_id": "owner", "revision": 9, "granted": False},
                {
                    "bot_id": "bot",
                    "owner_id": "other-owner",
                    "revision": 0,
                    "granted": False,
                },
            ]
        }

    world.catalog.grant_states = states
    targets = [
        {"bot_id": "bot", "owner_id": owner} for owner in ("owner", "other-owner")
    ]
    response = client.post(
        PREFIX + "/kb/bot-authorization-states", json={"targets": targets}
    )
    assert response.status_code == 200
    assert [item["revision"] for item in response.json()["items"]] == [9, 0]
    assert calls == [
        {
            "kb_id": "kb",
            "actor_id": "actor",
            "identities": [("bot", "owner"), ("bot", "other-owner")],
            "env": "pre",
        }
    ]


@pytest.mark.parametrize(
    "targets,extra,status",
    [
        ([], {}, 422),
        ([{"bot_id": "bot", "owner_id": "owner"}] * 2, {}, 422),
        ([{"bot_id": "bot", "owner_id": "owner"}], {"actor_id": "spoof"}, 422),
        ([{"bot_id": "outside", "owner_id": "owner"}], {}, 404),
    ],
)
def test_batch_rejects_invalid_or_cross_space(client, targets, extra, status):
    result = client.post(
        PREFIX + "/kb/bot-authorization-states", json={"targets": targets, **extra}
    )
    assert result.status_code == status


def test_batch_requires_native_manage(client, world):
    world.catalog.kb["can_manage"] = False
    result = client.post(
        PREFIX + "/kb/bot-authorization-states",
        json={"targets": [{"bot_id": "bot", "owner_id": "owner"}]},
    )
    assert result.status_code == 403


def test_exact_ecb_batch_includes_revoked_state():
    import json

    calls = []

    def send(request):
        calls.append(request)
        assert request.method == "POST"
        assert request.url.path == "/api/v1/knowledge/kb/kb/bot-authorization-states"
        assert json.loads(request.content) == {
            "actor_staff_no": "actor",
            "platform": "teamclaw",
            "targets": [
                {"bot_id": "bot", "user_id": owner}
                for owner in ("owner", "other-owner")
            ],
        }
        return httpx.Response(
            200,
            json={
                "kb_id": "kb",
                "platform": "teamclaw",
                "platform_env": "pre",
                "items": [
                    {
                        "bot_id": "bot",
                        "user_id": owner,
                        "granted": False,
                        "revision": revision,
                    }
                    for owner, revision in (("owner", 9), ("other-owner", 0))
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(send)) as http:
        result = make_catalog(
            "https://ecb.test", enabled=True, client=http
        ).grant_states(
            kb_id="kb",
            actor_id="actor",
            identities=[("bot", "owner"), ("bot", "other-owner")],
            env="pre",
        )
    assert [item["revision"] for item in result["items"]] == [9, 0]
    assert len(calls) == 1


def test_initialized_upstream_404_is_not_an_empty_catalog(client, world):
    from agentclaw.community.kernel.knowledge import KnowledgeError

    def reject(**kw):
        raise KnowledgeError("KNOWLEDGE_REMOTE_REJECTED", 404)

    world.catalog.list_in_space = reject
    result = client.get(PREFIX)
    assert result.status_code == 404


def test_nonmember_cannot_use_never_activated_empty_shortcut(client, world):
    world.service.sync = SimpleNamespace(get_context=lambda **kw: None)
    result = client.get("/api/knowledge/spaces/2/knowledge-bases")
    assert result.status_code == 403


@pytest.mark.parametrize(
    "context",
    [
        SimpleNamespace(space_id="2", environment="pre", revision=1),
        SimpleNamespace(space_id="1", environment="prod", revision=1),
        SimpleNamespace(space_id="1", environment="pre", revision=0),
    ],
)
def test_mismatched_sync_context_fails_closed(client, world, context):
    world.service.sync = SimpleNamespace(get_context=lambda **kw: context)
    response = client.get(PREFIX)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "KNOWLEDGE_SCOPE_MISMATCH"
    assert world.catalog.calls == []


def test_incomplete_exact_batch_never_invents_zero():
    from agentclaw.community.kernel.knowledge_management import KnowledgeManagementError

    calls = []

    def send(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "kb_id": "kb",
                "platform": "teamclaw",
                "platform_env": "pre",
                "items": [],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(send)) as http:
        with pytest.raises(KnowledgeManagementError, match="KNOWLEDGE_GRANTS_INVALID"):
            make_catalog("https://ecb.test", enabled=True, client=http).grant_states(
                kb_id="kb", actor_id="actor", identities=[("bot", "owner")], env="pre"
            )
    assert len(calls) == 1


@pytest.mark.parametrize(
    "page",
    [
        {"items": []},
        {"items": [], "next_cursor": 0},
        {"items": [{"kb_id": "other"}], "next_cursor": None},
        {
            "items": [
                {
                    "kb_id": "kb",
                    "platform": "teamclaw",
                    "platform_env": "pre",
                    "bot_id": "bot",
                    "user_id": "owner",
                    "granted": False,
                    "revision": True,
                }
            ],
            "next_cursor": None,
        },
    ],
)
def test_grant_malformed_response_is_not_absence(page):
    from agentclaw.community.kernel.knowledge_management import KnowledgeManagementError

    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=page))
    ) as http:
        catalog = make_catalog("https://ecb.test", enabled=True, client=http)
        with pytest.raises(KnowledgeManagementError, match="KNOWLEDGE_GRANTS_INVALID"):
            catalog.grant_states(
                kb_id="kb", actor_id="actor", identities=[("bot", "owner")], env="pre"
            )


def test_exact_batch_foreign_environment_fails_without_scanning():
    from agentclaw.community.kernel.knowledge_management import KnowledgeManagementError

    calls = []

    def send(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "kb_id": "kb",
                "platform": "teamclaw",
                "platform_env": "prod",
                "items": [
                    {
                        "bot_id": "bot",
                        "user_id": "owner",
                        "granted": False,
                        "revision": 12,
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(send)) as http:
        with pytest.raises(KnowledgeManagementError, match="KNOWLEDGE_GRANTS_INVALID"):
            make_catalog("https://ecb.test", enabled=True, client=http).grant_states(
                kb_id="kb", actor_id="actor", identities=[("bot", "owner")], env="pre"
            )
    assert len(calls) == 1


def test_exact_batch_has_one_budget_and_maps_each_owner_once():
    from agentclaw.community.adapters.knowledge_platform.grant_state_reader import (
        read_grant_states,
    )

    mapped, deadlines = [], []

    def identity(user):
        mapped.append(user)
        return user

    def read(path, params, *, method, body, deadline):
        deadlines.append(deadline)
        assert method == "POST" and params == {}
        return {
            "kb_id": "kb",
            "platform": "teamclaw",
            "platform_env": "pre",
            "items": [
                {**target, "revision": 0, "granted": False}
                for target in body["targets"]
            ],
        }

    catalog = SimpleNamespace(
        platform="teamclaw", identities=SimpleNamespace(staff_no=identity), _read=read
    )
    result = read_grant_states(
        catalog,
        kb_id="kb",
        actor_id="actor",
        identities=[("a", "owner"), ("b", "owner")],
        env="pre",
    )
    assert mapped == ["actor", "owner"] and len(deadlines) == 1
    assert [row["revision"] for row in result["items"]] == [0, 0]


def test_business_job_hung_upstream_obeys_shared_timeout(monkeypatch):
    import asyncio

    from agentclaw.community.adapters.knowledge_platform import job_reader
    from agentclaw.community.kernel.knowledge import KnowledgeError

    real_timeout = asyncio.timeout
    budgets, cancelled = [], []

    def timeout(seconds):
        budgets.append(seconds)
        return real_timeout(0.01)

    async def call(*args, **kwargs):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    monkeypatch.setattr(job_reader.asyncio, "timeout", timeout)
    with pytest.raises(KnowledgeError) as error:
        asyncio.run(
            job_reader.read_business_job(
                SimpleNamespace(call=call), kb_id="kb", job_id="job", headers={}
            )
        )
    assert (
        error.value.code == "KNOWLEDGE_JOB_LOOKUP_TIMEOUT" and error.value.status == 503
    )
    assert budgets == [8] and cancelled == [True]
