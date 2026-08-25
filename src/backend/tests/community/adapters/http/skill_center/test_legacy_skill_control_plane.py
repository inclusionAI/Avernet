"""The published /api/skills Local commands delegate to Installation control."""

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from agentclaw.community.adapters.http.skill_center.schemas import (
    ActivateRequest,
    AddSkillsRequest,
    DeactivateSkillSetRequest,
    SearchRequest,
)
from agentclaw.community.adapters.http.skill_center.skillsets import (
    add_skills_to_set,
    get_default_skill_set,
    get_skill_set_mcps,
)
from agentclaw.community.adapters.http.skill_center.skills import (
    activate_skill,
    deactivate_skill,
    deactivate_skill_set,
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
    SkillSetControlPlaneNotFoundError,
)


class _Bots:
    def get_by_id_and_owner(self, bot_id: str, owner_id: str):
        assert (bot_id, owner_id) == ("bot", "owner")
        return {"active_engine": "openclaw", "bot_type": "personal"}


class _Assets:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool | None]] = []

    def get_skill(self, *, skill_id: str, bot_id: str, owner_id: str, user_id: str):
        assert (skill_id, bot_id, owner_id, user_id) == ("7", "bot", "owner", "owner")
        self.calls.append(("get", None))
        return {"name": "local-seven"}

    def resolve_legacy_skill_id(
        self,
        *,
        skill_reference: str,
        source_path: str,
        bot_id: str,
        owner_id: str,
        user_id: str,
    ):
        assert (skill_reference, source_path, bot_id, owner_id, user_id) in {
            ("legacy/path", "legacy", "bot", "owner", "owner"),
            ("legacy-link", "legacy/path", "bot", "owner", "owner"),
        }
        return "7"

    async def set_active(
        self, *, skill_id: str, bot_id: str, owner_id: str, user_id: str, active: bool
    ):
        assert (skill_id, bot_id, owner_id, user_id) == ("7", "bot", "owner", "owner")
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


class _AddressedBots:
    def get_by_id_and_owner(self, bot_id: str, owner_id: str):
        assert (bot_id, owner_id) == ("persisted-bot", "owner")
        return {
            "active_engine": "claude_code",
            "bot_type": "personal",
            "template_type": "normalCC",
        }


class _LegacySetScopeControlPlane:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def resolve_legacy_set_scope(self, **kwargs):
        self.calls.append(("resolve", kwargs))
        return SimpleNamespace(owner_id="owner", bot_id="persisted-bot")

    def get_set(self, **kwargs):
        self.calls.append(("get_set", kwargs))
        assert kwargs["bot_id"] == "persisted-bot"
        assert kwargs["owner_id"] == "owner"
        return {"id": "set-1", "is_default": False}

    def list_mcps(self, **kwargs):
        self.calls.append(("list_mcps", kwargs))
        assert kwargs["bot_id"] == "persisted-bot"
        assert kwargs["owner_id"] == "owner"
        return [
            {
                "id": "mcp-1",
                "server_code": "mcp.example",
                "name": "Example MCP",
                "description": None,
                "icon": None,
            }
        ]

    async def deactivate(self, **kwargs):
        self.calls.append(("deactivate", kwargs))
        assert kwargs["bot_id"] == "persisted-bot"
        assert kwargs["owner_id"] == "owner"
        return {"id": "set-1", "changed": True}


@pytest.mark.asyncio
async def test_legacy_mcp_read_recovers_non_default_bot_from_exact_set_id() -> None:
    control_plane = _LegacySetScopeControlPlane()

    response = await get_skill_set_mcps(
        "set-1",
        user_id="owner",
        entity_id=None,
        entity_type=None,
        bot_id=None,
        engine_type=None,
        ctx=SimpleNamespace(user_id="owner", bot_id="default"),
        bot_repo=_AddressedBots(),
        skill_set_service_factory=object(),
        control_plane=control_plane,
    )

    assert response.model_dump() == {
        "success": True,
        "data": [
            {
                "id": "mcp-1",
                "server_code": "mcp.example",
                "name": "Example MCP",
                "description": None,
                "icon": None,
                "status": "ONLINE",
            }
        ],
        "count": 1,
    }
    assert control_plane.calls[0] == (
        "resolve",
        {
            "set_id": "set-1",
            "actor_id": "owner",
            "owner_id_hint": None,
        },
    )


@pytest.mark.asyncio
async def test_legacy_mcp_read_never_redirects_an_explicit_bot_address() -> None:
    class _ExplicitControlPlane(_LegacySetScopeControlPlane):
        def resolve_legacy_set_scope(self, **_kwargs):
            raise AssertionError("explicit bot_id must not enter legacy fallback")

    control_plane = _ExplicitControlPlane()

    await get_skill_set_mcps(
        "set-1",
        user_id="owner",
        entity_id="owner",
        entity_type=None,
        bot_id="persisted-bot",
        engine_type=None,
        ctx=SimpleNamespace(user_id="owner", bot_id="default"),
        bot_repo=_AddressedBots(),
        skill_set_service_factory=object(),
        control_plane=control_plane,
    )

    assert control_plane.calls[0][0] == "get_set"


@pytest.mark.asyncio
async def test_legacy_deactivate_recovers_non_default_bot_from_exact_set_id() -> None:
    control_plane = _LegacySetScopeControlPlane()

    response = await deactivate_skill_set(
        DeactivateSkillSetRequest(skill_set_id="set-1"),
        ctx=SimpleNamespace(user_id="owner", bot_id="default"),
        bot_repo=_AddressedBots(),
        control_plane=control_plane,
    )

    assert response.model_dump() == {
        "success": True,
        "message": "Skill set deactivated",
        "data": {"deactivated": ["set-1"], "failed": []},
    }
    assert control_plane.calls[0] == (
        "resolve",
        {
            "set_id": "set-1",
            "actor_id": "owner",
            "owner_id_hint": None,
        },
    )


@pytest.mark.asyncio
async def test_legacy_activate_keeps_wire_but_uses_bot_skill_asset_control_plane() -> (
    None
):
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
        "7",
        ActivateRequest(source_path="legacy", relative_path="legacy/path"),
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
    assert assets.calls == [("get", None), ("set", True)]


@pytest.mark.asyncio
async def test_legacy_activate_with_link_name_resolves_decimal_id_before_control_plane() -> (
    None
):
    assets = _Assets()
    await activate_skill(
        "legacy-link",
        ActivateRequest(source_path="legacy/path"),
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
    assert assets.calls == [("get", None), ("set", True)]


@pytest.mark.asyncio
async def test_bound_control_plane_not_found_never_falls_back_to_legacy_mutation() -> (
    None
):
    class _MissingAssets(_Assets):
        def resolve_legacy_skill_id(self, **_kwargs):
            raise LocalSkillNotFoundError()

    legacy_service_factory = MagicMock()
    with pytest.raises(LocalSkillNotFoundError):
        await activate_skill(
            "missing-link",
            ActivateRequest(source_path="missing/path"),
            bot_id="bot",
            ctx=SimpleNamespace(user_id="owner", bot_id="bot"),
            bot_repo=_Bots(),
            path_factory=object(),
            skill_service_factory=legacy_service_factory,
            skill_set_service_factory=object(),
            resolver=object(),
            device_sync_dispatcher=object(),
            asset_service=_MissingAssets(),
        )

    legacy_service_factory.create.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_deactivate_keeps_wire_but_uses_bot_skill_asset_control_plane() -> (
    None
):
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
        def get_set(self, **_kwargs):
            return {"id": "set-1", "is_default": False}

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
async def test_legacy_skill_set_batch_validates_target_before_materialising_asset() -> (
    None
):
    class _ControlPlane:
        resolved = False

        def get_set(self, **_kwargs):
            raise SkillSetControlPlaneNotFoundError()

        def resolve_legacy_skill_id(self, **_kwargs):
            self.resolved = True
            return "7"

    control_plane = _ControlPlane()
    # The route raises the situation, not a status: ``adapters.http.app``
    # answers 404 for this class. See test_skillset_error_status_mapping.py.
    with pytest.raises(SkillSetControlPlaneNotFoundError):
        await add_skills_to_set(
            "missing-set",
            AddSkillsRequest(skill_ids=["7"], user_id="owner", bot_id="bot"),
            entity_id=None,
            entity_type=None,
            bot_id=None,
            engine_type=None,
            ctx=SimpleNamespace(user_id="owner", bot_id="bot"),
            bot_repo=_Bots(),
            control_plane=control_plane,
        )

    assert control_plane.resolved is False


@pytest.mark.asyncio
async def test_legacy_skill_set_batch_propagates_infrastructure_failure() -> None:
    class _ControlPlane:
        def get_set(self, **_kwargs):
            return {"id": "set-1", "is_default": False}

        def resolve_legacy_skill_id(self, **kwargs):
            return kwargs["identifier"]

        async def add_skill(self, **_kwargs):
            raise RuntimeError("database unavailable")

    # The route used to catch this and return a 500 reading "Skill set
    # operation failed", which named the endpoint rather than the fault and
    # left "database unavailable" nowhere in the response or the traceback the
    # caller could act on. It now escapes to the app's catch-all, which logs
    # the real exception with its traceback before answering 500.
    with pytest.raises(RuntimeError, match="database unavailable"):
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


@pytest.mark.asyncio
async def test_legacy_skill_set_batch_does_not_hide_mutation_busy() -> None:
    class _ControlPlane:
        def get_set(self, **_kwargs):
            return {"id": "set-1", "is_default": False}

        def resolve_legacy_skill_id(self, **kwargs):
            return kwargs["identifier"]

        async def add_skill(self, **_kwargs):
            raise SkillSetControlPlaneConflictError("BOT_MUTATION_BUSY")

    # A busy fence is not one of the two per-skill conflicts the batch records
    # as a partial failure, so it must abort the request. The reason code is
    # the published wire and survives to the caller as the error detail.
    with pytest.raises(SkillSetControlPlaneConflictError) as failure:
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

    assert str(failure.value) == "BOT_MUTATION_BUSY"


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


@pytest.mark.asyncio
async def test_legacy_remove_reaches_the_default_set_exclusion_wire() -> None:
    """The legacy DELETE has no default pre-refusal: the control plane's
    restored opt-out (remove on a Default = per-Bot exclusion) flows through
    the seam, and the historical wire shape is preserved."""
    from agentclaw.community.adapters.http.skill_center.skillsets import (
        remove_skill_from_set,
    )

    class _ControlPlane(_LegacySetScopeControlPlane):
        def get_set(self, **kwargs):
            self.calls.append(("get_set", kwargs))
            return {"id": "set-1", "is_default": True}

        async def remove_skill(self, **kwargs):
            self.calls.append(("remove_skill", kwargs))
            return {"id": "set-1", "is_default": True, "changed": True}

    control_plane = _ControlPlane()
    response = await remove_skill_from_set(
        "set-1",
        "7",
        user_id="owner",
        entity_id=None,
        entity_type=None,
        bot_id=None,
        engine_type=None,
        ctx=SimpleNamespace(user_id="owner", bot_id="default"),
        bot_repo=_AddressedBots(),
        control_plane=control_plane,
    )

    assert response.model_dump() == {
        "success": True,
        "message": "Skill removed from skill set",
    }
    assert ("remove_skill", {
        "bot_id": "persisted-bot",
        "owner_id": "owner",
        "user_id": "owner",
        "set_id": "set-1",
        "skill_id": "7",
    }) in control_plane.calls
