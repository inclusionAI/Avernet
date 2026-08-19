"""The published /api/skills Local commands delegate to Installation control."""

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from agentclaw.community.adapters.http.skill_center.schemas import (
    ActivateRequest,
    AddSkillsRequest,
    SearchRequest,
)
from agentclaw.community.adapters.http.skill_center.skillsets import (
    add_skills_to_set,
    get_default_skill_set,
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
from agentclaw.community.core.skill_center.errors import (
    LocalSkillNotFoundError,
    SkillSetControlPlaneConflictError,
)


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

    def resolve_legacy_skill_id(
        self, *, skill_reference: str, source_path: str, bot_id: str, actor_id: str
    ):
        assert (skill_reference, source_path, bot_id, actor_id) in {
            ("legacy/path", "legacy", "bot", "owner"),
            ("legacy-link", "legacy/path", "bot", "owner"),
        }
        return "7"

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
        return {
            "status": "completed",
            "result": {"synced": True, "database": {"failed": 0}},
        }



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
async def test_legacy_activate_with_link_name_resolves_decimal_id_before_control_plane() -> None:
    assets = _Assets()
    await activate_skill(
        "legacy-link", ActivateRequest(source_path="legacy/path"),
        bot_id="bot", ctx=SimpleNamespace(user_id="owner", bot_id="bot"),
        bot_repo=_Bots(), path_factory=object(), skill_service_factory=object(),
        skill_set_service_factory=object(), resolver=object(),
        device_sync_dispatcher=object(), asset_service=assets,
    )
    assert assets.calls == [("get", None), ("set", True)]


@pytest.mark.asyncio
async def test_bound_control_plane_not_found_never_falls_back_to_legacy_mutation() -> None:
    class _MissingAssets(_Assets):
        def resolve_legacy_skill_id(self, **_kwargs):
            raise LocalSkillNotFoundError()

    legacy_service_factory = MagicMock()
    with pytest.raises(LocalSkillNotFoundError):
        await activate_skill(
            "missing-link", ActivateRequest(source_path="missing/path"),
            bot_id="bot", ctx=SimpleNamespace(user_id="owner", bot_id="bot"),
            bot_repo=_Bots(), path_factory=object(),
            skill_service_factory=legacy_service_factory,
            skill_set_service_factory=object(), resolver=object(),
            device_sync_dispatcher=object(), asset_service=_MissingAssets(),
        )

    legacy_service_factory.create.assert_not_called()


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
    assert synced.data["db_sync"] == {"failed": 0}
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


@pytest.mark.asyncio
async def test_legacy_sync_exposes_partial_database_failure_in_historical_db_sync_field():
    class _PartialCatalog(_Catalog):
        def sync(self):
            return {
                "status": "failed",
                "message": "Database scan failed",
                "result": {
                    "synced": True,
                    "database": {"created": 1, "failed": 1, "errors": ["row"]},
                },
            }

    response = await sync_market(
        repository_catalog=_PartialCatalog(),
        ctx=SimpleNamespace(user_id="owner", bot_id="bot"),
    )
    assert response.success is True
    assert response.data["synced"] is False
    assert response.data["db_sync"]["failed"] == 1


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


@pytest.mark.asyncio
async def test_legacy_skill_set_batch_keeps_domain_partial_success() -> None:
    class _ControlPlane:
        def resolve_legacy_skill_id(self, **kwargs):
            return kwargs["identifier"]

        async def add_skill(self, **_kwargs):
            raise SkillSetControlPlaneConflictError(
                "RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET"
            )

    response = await add_skills_to_set(
        "set-1",
        AddSkillsRequest(skill_ids=["7"], user_id="owner", bot_id="bot"),
        entity_id=None,
        entity_type=None,
        bot_id=None,
        engine_type=None,
        ctx=SimpleNamespace(user_id="owner", bot_id="bot"),
        bot_repo=_Bots(),
        control_plane=_ControlPlane(),
    )

    assert response.success is True
    assert response.data["success"] == []
    assert response.data["failed"][0]["skill_id"] == "7"


@pytest.mark.asyncio
async def test_legacy_skill_set_batch_propagates_infrastructure_failure() -> None:
    class _ControlPlane:
        def resolve_legacy_skill_id(self, **kwargs):
            return kwargs["identifier"]

        async def add_skill(self, **_kwargs):
            raise RuntimeError("database unavailable")

    with pytest.raises(HTTPException) as failure:
        await add_skills_to_set(
            "set-1",
            AddSkillsRequest(skill_ids=["7"], user_id="owner", bot_id="bot"),
            entity_id=None,
            entity_type=None,
            bot_id=None,
            engine_type=None,
            ctx=SimpleNamespace(user_id="owner", bot_id="bot"),
            bot_repo=_Bots(),
            control_plane=_ControlPlane(),
        )

    assert failure.value.status_code == 500
    assert failure.value.detail == "Skill set operation failed"


@pytest.mark.asyncio
async def test_legacy_skill_set_batch_does_not_hide_mutation_busy() -> None:
    class _ControlPlane:
        def resolve_legacy_skill_id(self, **kwargs):
            return kwargs["identifier"]

        async def add_skill(self, **_kwargs):
            raise SkillSetControlPlaneConflictError("BOT_MUTATION_BUSY")

    with pytest.raises(HTTPException) as failure:
        await add_skills_to_set(
            "set-1",
            AddSkillsRequest(skill_ids=["7"], user_id="owner", bot_id="bot"),
            entity_id=None,
            entity_type=None,
            bot_id=None,
            engine_type=None,
            ctx=SimpleNamespace(user_id="owner", bot_id="bot"),
            bot_repo=_Bots(),
            control_plane=_ControlPlane(),
        )

    assert failure.value.status_code == 400
    assert failure.value.detail == "BOT_MUTATION_BUSY"


@pytest.mark.asyncio
async def test_legacy_default_detail_projects_historical_false_as_active() -> None:
    class _Service:
        def get_default_skill_set(self, **_kwargs):
            return {
                "id": "1",
                "name": "Default",
                "description": None,
                "is_default": True,
                "is_builtin": True,
                "is_active": False,
                "user_id": "owner",
                "bolt_id": "bot",
                "engine_type": "openclaw",
                "gmt_created": "",
                "gmt_modified": "",
            }

    factory = SimpleNamespace(create=lambda **_kwargs: _Service())
    response = await get_default_skill_set(
        user_id="owner",
        entity_id="owner",
        entity_type="staff",
        bot_id="bot",
        engine_type="openclaw",
        ctx=SimpleNamespace(user_id="owner", bot_id="bot"),
        bot_repo=_Bots(),
        skill_set_service_factory=factory,
    )

    assert response.data.is_active is True
