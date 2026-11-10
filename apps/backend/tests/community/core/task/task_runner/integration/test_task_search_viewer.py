"""TaskService -> semantic recommendation -> actual BCS parameter mapping."""

from types import SimpleNamespace

import httpx
import pytest

from agentclaw.community.core.bot_public.services.bot_catalog_metadata_service import (
    BcsBotCatalogMetadataService,
)
from agentclaw.community.core.bot_public.services.bot_discover_service import (
    BotDiscoverService,
)
from agentclaw.community.core.task.task_center.task_service_relay import (
    TaskServiceRelayMixin,
)
from agentclaw.community.core.task.task_dispatch.search import TaskSearch
from agentclaw.community.core.task.task_runner.client.candidate_search import (
    search_candidates,
)
from agentclaw.community.core.task.task_runner.client.semantic_keyword_bot_discover import (
    SemanticKeywordBotDiscover,
)
from tests.community.core.bot_public.test_bot_discover_service import _backend_bot


class _Service(TaskServiceRelayMixin):
    def __init__(self, search):
        self.events = []
        self._graph = SimpleNamespace(
            append_action_event=lambda *a, **kw: self.events.append((a, kw))
        )
        self._relay_adapter = SimpleNamespace(search=search)


@pytest.mark.asyncio
@pytest.mark.parametrize("viewer_type", [None, "bot", "human"])
async def test_task_search_uses_product_viewer_rules_and_preserves_readiness(
    monkeypatch, viewer_type
):
    # Target metadata is independent from the semantic index runtime-state fact.
    rows = [
        ("protected", "protected", "private", True, "ACTIVE", "offline"),
        ("human-only", "private", "public", True, "ACTIVE", "online"),
        ("public", "public", "private", True, "ACTIVE", "online"),
        ("disconnected", "public", "public", False, "ACTIVE", "online"),
        ("inactive", "public", "public", True, "OFFLINE", "online"),
        ("both", "public", "public", True, "ACTIVE", "online"),
    ]
    records = {
        name: {
            "bot_uuid": f"{name}:owner",
            "name": name,
            "actor_kind": "bot",
            "status": "online",
            "visibility": vis,
            "user_visibility": uvis,
            "is_online": online,
            "created_by": "owner",
        }
        for name, vis, uvis, online, lifecycle, indexed in rows
    }
    calls, recommendations = [], []

    class Http:
        def get(self, path, *, params, timeout):
            calls.append(dict(params))
            assert path == "/bots/search"
            items = []
            # Emulate BCS's existing per-viewer defaults, not recommendation rules.
            vis = params.get(
                "visibility",
                "public,protected,private"
                if params.get("viewer_actor_type") == "human"
                else "public,protected",
            ).split(",")
            uvis = params.get(
                "user_visibility",
                "public,protected"
                if params.get("viewer_actor_type") != "bot"
                else "public,protected,private",
            ).split(",")
            for uuid in params["bot_uuids"].split(","):
                row = records[uuid.rsplit(":", 1)[0]]
                if row["visibility"] in vis and row["user_visibility"] in uvis:
                    items.append(row)
            return httpx.Response(
                200,
                json={"items": items, "total": len(items)},
                request=httpx.Request("GET", "https://bcs.fixture/bots/search"),
            )

    class Repo:
        def list_bots_by_owner_bot_pairs(self, pairs, page, page_size):
            items = []
            for name, owner in pairs:
                item = _backend_bot()
                item.update(
                    bot_id=name,
                    bot_name=name,
                    owner_id=owner,
                    entity_id=owner,
                    status=next(row[4] for row in rows if row[0] == name),
                )
                items.append(item)
            return len(items), items

    semantic = BotDiscoverService(
        Repo(),
        SimpleNamespace(base_url="https://semantic.fixture"),
        BcsBotCatalogMetadataService(Http()),
    )

    def recommend(**kwargs):
        recommendations.append(kwargs)
        filters = kwargs["filters"]
        found = [
            {"worker_id": f"{name}:owner", "score": 0.9}
            for name, vis, uvis, online, lifecycle, indexed in rows
            if indexed in filters["runtime_state"]
            and ("availability" not in filters or vis in filters["availability"])
        ]
        return {"recommendations": found}

    monkeypatch.setattr(semantic, "_call_bcsfuse_recommend", recommend)
    service = _Service(TaskSearch(SemanticKeywordBotDiscover(None, semantic)))
    options = (
        {}
        if viewer_type is None
        else {
            "viewer_actor_type": viewer_type,
            "viewer_actor_id": "viewer:owner" if viewer_type == "bot" else "146836",
        }
    )
    result = await service.search_bots(
        query="测试发布的bot", task_id="task", node_id="node", relay_token="generation-1", **options
    )
    expected = {
        None: ["public:owner", "both:owner"],
        "bot": ["protected:owner", "public:owner", "both:owner"],
        "human": ["human-only:owner", "both:owner"],
    }
    assert [item.bot_id for item in result] == expected[viewer_type]
    assert recommendations[0]["top_k"] == 5
    params = calls[0]
    if viewer_type is None:
        assert recommendations[0]["filters"] == {"runtime_state": ["online"]}
        assert params["visibility"] == "public"
        assert params["user_visibility"] == "public,protected,private"
        assert params["status"] == "online" and params["tc_bot"] is True
        assert "viewer_actor_type" not in params
    elif viewer_type == "bot":
        assert recommendations[0]["filters"] == {
            "availability": ["public", "protected"],
            "runtime_state": ["online", "offline"],
        }
        assert params["visibility"] == "public,protected"
        assert params["viewer_actor_id"] == "viewer:owner"
        assert "tc_bot" not in params and "status" not in params
    else:
        assert recommendations[0]["filters"] == {
            "availability": ["public", "protected", "private"],
            "runtime_state": ["online"],
        }
        assert params["user_visibility"] == "public,protected"
        assert params["viewer_actor_id"] == "146836" and params["tc_bot"] is True
        assert "visibility" not in params


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [
        {"viewer_actor_type": "bot"},
        {"viewer_actor_id": "bot:owner"},
        {"viewer_actor_type": "admin", "viewer_actor_id": "id"},
        {"viewer_actor_type": "human", "viewer_actor_id": 123},
    ],
)
async def test_service_rejects_invalid_viewer_before_search(options):
    class NoSearch:
        async def search_catalog(self, *args, **kwargs):
            pytest.fail("invalid viewer must not reach search")

    with pytest.raises(ValueError):
        await _Service(NoSearch()).search_bots(
            query="q", task_id="t", node_id="n", relay_token="generation-1", **options
        )


@pytest.mark.asyncio
async def test_explicit_viewer_is_not_silently_dropped_on_legacy_adapter():
    class Legacy:
        def search_by_keyword(self, **kwargs):
            pytest.fail("legacy unscoped search must not run")

    with pytest.raises(ValueError, match="viewer"):
        await search_candidates(
            Legacy(), "q", viewer_actor_type="bot", viewer_actor_id="bot:owner"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("viewer_type", ["bot", "human"])
async def test_explicit_viewer_keeps_five_candidate_cap_and_runtime_filter(viewer_type):
    calls = []

    class Semantic:
        def search_by_keyword(self, **kwargs):
            calls.append(kwargs)
            return {
                "items": [
                    {
                        "bot_uuid": "offline:owner",
                        "status": "ACTIVE",
                        "is_online": False,
                        "recommend": {"score": 1.0},
                    },
                    *[
                        {
                            "bot_uuid": f"b{i}:owner",
                            "status": "ACTIVE",
                            "is_online": True,
                            "recommend": {"score": i / 10},
                        }
                        for i in range(7)
                    ],
                ]
            }

    service = _Service(TaskSearch(SemanticKeywordBotDiscover(None, Semantic())))
    candidates = await service.search_bots(
        query="q",
        task_id="t",
        node_id="n",
        relay_token="generation-1",
        viewer_actor_type=viewer_type,
        viewer_actor_id="  viewer-id  ",
    )
    assert [item.bot_id for item in candidates] == [
        f"b{i}:owner" for i in [6, 5, 4, 3, 2]
    ]
    assert calls[0]["viewer_actor_id"] == "viewer-id"
    assert "catalog_filters" not in calls[0] and "filters" not in calls[0]
    assert calls[0]["runtime_state"] == "online"
