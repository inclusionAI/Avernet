"""The published /api/skills Local commands delegate to Installation control."""

import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from agentclaw.community.adapters.http.skill_center.schemas import (
    ActivateRequest,
    SearchRequest,
)
from agentclaw.community.adapters.http.skill_center.skills import (
    activate_skill,
    deactivate_skill,
    get_market_tree,
    list_local_market_skills,
    list_market_skills,
    search_market_skills,
    sync_market,
)
from agentclaw.community.adapters.http.dependencies import get_request_context


class _Bots:
    def get_by_id_and_owner(self, bot_id: str, owner_id: str):
        assert (bot_id, owner_id) == ("bot", "owner")
        return {"active_engine": "openclaw", "bot_type": "personal"}


class _Assets:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool | None]] = []

    def get_skill(self, *, skill_id: str, bot_id: str, actor_id: str):
        assert (skill_id, bot_id, actor_id) == ("7", "bot", "owner")
        self.calls.append(("get", None))
        return {"name": "local-seven"}

    async def set_active(self, *, skill_id: str, bot_id: str, actor_id: str, active: bool):
        assert (skill_id, bot_id, actor_id) == ("7", "bot", "owner")
        self.calls.append(("set", active))
        return {"name": "local-seven"}


class _Catalog:
    def __init__(self, *, sync_status: str = "completed") -> None:
        self.calls: list[tuple[str, object]] = []
        self.sync_status = sync_status

    def list(self, *, path=None, orderby=None):
        self.calls.append(("list", (path, orderby)))
        return [{"id": "1"}]

    def search(self, *, keyword: str, limit: int = 100):
        self.calls.append(("search", (keyword, limit)))
        return [{"id": "1"}]

    def tree(self):
        self.calls.append(("tree", None))
        return [{"name": "tools"}]

    def sync(self):
        self.calls.append(("sync", None))
        if self.sync_status == "failed":
            return {"status": "failed", "message": "private failure"}
        return {"status": "completed", "result": {"synced": True}}



@pytest.mark.asyncio
async def test_legacy_activate_keeps_wire_but_uses_bot_skill_asset_control_plane() -> None:
    assets = _Assets()
    response = await activate_skill(
        "7",
        ActivateRequest(source_path="local://ignored"),
        bot_id="bot",
        ctx=SimpleNamespace(user_id="owner", bot_id="bot"),
        bot_repo=_Bots(),
        path_factory=object(),
        skill_service_factory=object(),
        skill_set_service_factory=object(),
        resolver=object(),
        device_sync_dispatcher=object(),
        asset_service=assets,
    )

    assert response.model_dump() == {
        "success": True,
        "message": "Skill activated successfully",
        "link_name": "local-seven",
    }
    assert assets.calls == [("get", None), ("set", True)]


@pytest.mark.asyncio
async def test_legacy_activate_with_relative_path_still_uses_control_plane() -> None:
    assets = _Assets()
    await activate_skill(
        "7", ActivateRequest(source_path="legacy", relative_path="legacy/path"),
        bot_id="bot", ctx=SimpleNamespace(user_id="owner", bot_id="bot"),
        bot_repo=_Bots(), path_factory=object(), skill_service_factory=object(),
        skill_set_service_factory=object(), resolver=object(),
        device_sync_dispatcher=object(), asset_service=assets,
    )
    assert assets.calls == [("get", None), ("set", True)]


@pytest.mark.asyncio
async def test_legacy_deactivate_keeps_wire_but_uses_bot_skill_asset_control_plane() -> None:
    assets = _Assets()
    response = await deactivate_skill(
        "7",
        bot_id="bot",
        ctx=SimpleNamespace(user_id="owner", bot_id="bot"),
        bot_repo=_Bots(),
        path_factory=object(),
        skill_service_factory=object(),
        skill_set_service_factory=object(),
        resolver=object(),
        device_sync_dispatcher=object(),
        asset_service=assets,
    )

    assert response.model_dump() == {
        "success": True,
        "message": "Skill deactivated successfully",
    }
    assert assets.calls == [("get", None), ("set", False)]


@pytest.mark.asyncio
async def test_legacy_repo_catalog_accepts_historical_bot_query_wire() -> None:
    catalog = _Catalog()
    ignored = {
        "entity_id": "entity",
        "entity_type": "team",
        "bot_id": "old-bot",
        "engine_type": "hermes",
    }
    ctx = SimpleNamespace(user_id="owner", bot_id="old-bot")

    local = await list_local_market_skills(
        repository_catalog=catalog, ctx=ctx, **ignored
    )
    tree = await get_market_tree(repository_catalog=catalog, ctx=ctx, **ignored)
    listed = await list_market_skills(
        path="tools", orderby="hotest", repository_catalog=catalog, ctx=ctx, **ignored
    )
    searched = await search_market_skills(
        SearchRequest(query="report"), repository_catalog=catalog, ctx=ctx, **ignored
    )
    synced = await sync_market(repository_catalog=catalog, ctx=ctx, **ignored)

    assert local.data == [{"id": "1"}]
    assert tree.data == [{"name": "tools"}]
    assert listed.data == [{"id": "1"}]
    assert searched.data == [{"id": "1"}]
    assert synced.data["synced"] is True
    assert catalog.calls == [
        ("list", (None, None)),
        ("tree", None),
        ("list", ("tools", "hotest")),
        ("search", ("report", 100)),
        ("sync", None),
    ]


@pytest.mark.asyncio
async def test_legacy_market_list_rejects_invalid_orderby_and_sync_keeps_failure_wire():
    ctx = SimpleNamespace(user_id="owner", bot_id="old-bot")
    with pytest.raises(HTTPException) as invalid_order:
        await list_market_skills(
            orderby="popular", repository_catalog=_Catalog(), ctx=ctx
        )
    assert invalid_order.value.status_code == 400
    assert invalid_order.value.detail == "orderby must be 'latest' or 'hotest'"

    with pytest.raises(HTTPException) as sync_failure:
        await sync_market(repository_catalog=_Catalog(sync_status="failed"), ctx=ctx)
    assert sync_failure.value.status_code == 500
    assert sync_failure.value.detail == "private failure"


def test_legacy_repo_catalog_routes_retain_request_context_auth_dependency() -> None:
    for handler in (
        list_local_market_skills,
        get_market_tree,
        list_market_skills,
        search_market_skills,
        sync_market,
    ):
        dependency = next(
            parameter.default
            for parameter in inspect.signature(handler).parameters.values()
            if parameter.name == "ctx"
        )
        assert dependency.dependency is get_request_context
