"""The published /api/skills Local commands delegate to Installation control."""

import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from agentclaw.community.adapters.http.skill_center.schemas import (
    AddSkillsRequest,
    ActivateSkillSetRequest,
    DeactivateSkillSetRequest,
    SearchRequest,
    UpdateSkillSetRequest,
)
from agentclaw.community.adapters.http.skill_center.skillsets import (
    add_skills_to_set,
    delete_skill_set,
    get_default_skill_set,
    get_skill_set,
    get_skill_set_mcps,
    get_skill_set_skills,
    remove_skill_from_set,
    update_skill_set,
)
from agentclaw.community.adapters.http.skill_center.skills import (
    activate_skill_set,
    deactivate_skill_set,
    get_market_tree,
    list_local_market_skills,
    list_market_skills,
    search_market_skills,
    sync_market,
)
from agentclaw.community.adapters.http.dependencies import get_request_context
from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneConflictError,
    SkillSetControlPlaneNotFoundError,
)
from agentclaw.community.core.skill_center.skill_set_batch import (
    SkillSetSkillOutcome,
)


class _Bots:
    def get_by_id_and_owner(self, bot_id: str, owner_id: str):
        assert (bot_id, owner_id) == ("bot", "owner")
        return {"active_engine": "openclaw", "bot_type": "personal"}


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

    async def activate(self, **kwargs):
        self.calls.append(("activate", kwargs))
        assert kwargs["bot_id"] == "persisted-bot"
        assert kwargs["owner_id"] == "owner"
        return {
            "id": "set-1",
            "changed": True,
            "runtime_projection": {
                "status": "DEGRADED",
                "components": {"skills": "DEGRADED"},
                "pending_count": 0,
                "degraded_count": 1,
                "issues": [
                    {
                        "resource_type": "SKILL",
                        "code": "UNMANAGED_ACTIVE_ENTRY_RETAINED",
                        "reason": "Bot 生效目录中存在同名实体目录",
                        "status": "DEGRADED",
                        "retryable": False,
                    }
                ],
            },
        }


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
            "owner_id_hint": "owner",
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
async def test_legacy_activate_keeps_success_wire_and_adds_degraded_diagnostics() -> None:
    control_plane = _LegacySetScopeControlPlane()

    response = await activate_skill_set(
        ActivateSkillSetRequest(skill_set_id="set-1"),
        ctx=SimpleNamespace(user_id="owner", bot_id="default"),
        bot_repo=_AddressedBots(),
        control_plane=control_plane,
    )

    assert response.model_dump() == {
        "success": True,
        "message": "能力集状态已保存，但部分 Skill 未完成运行时收敛",
        "data": {
            "activated": ["set-1"],
            "failed": [],
            "runtime_projection": {
                "status": "DEGRADED",
                "components": {"skills": "DEGRADED"},
                "pending_count": 0,
                "degraded_count": 1,
                "issues": [
                    {
                        "resource_type": "SKILL",
                        "code": "UNMANAGED_ACTIVE_ENTRY_RETAINED",
                        "reason": "Bot 生效目录中存在同名实体目录",
                        "status": "DEGRADED",
                        "retryable": False,
                    }
                ],
            },
        },
    }


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

        async def add_skills(self, **_kwargs):
            return [
                SkillSetSkillOutcome(
                    skill_id="7",
                    error=SkillSetControlPlaneConflictError(
                        "RESOURCE_ALREADY_IN_ANOTHER_SKILL_SET"
                    ),
                )
            ]

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
async def test_legacy_skill_set_batch_uses_body_owner_for_collaborator() -> None:
    class _ControlPlane:
        def get_set(self, **kwargs):
            assert kwargs == {
                "bot_id": "bot",
                "owner_id": "owner",
                "user_id": "collaborator",
                "set_id": "set-1",
            }
            return {"id": "set-1", "is_default": False}

        def resolve_legacy_skill_id(self, **kwargs):
            assert kwargs["bot_id"] == "bot"
            assert kwargs["owner_id"] == "owner"
            assert kwargs["actor_id"] == "collaborator"
            return kwargs["identifier"]

        async def add_skills(self, **kwargs):
            assert kwargs == {
                "bot_id": "bot",
                "owner_id": "owner",
                "user_id": "collaborator",
                "set_id": "set-1",
                "skill_ids": ["7", "8"],
            }
            return [
                SkillSetSkillOutcome(skill_id="7", changed=True),
                SkillSetSkillOutcome(skill_id="8", changed=True),
            ]

    response = await add_skills_to_set(
        "set-1",
        AddSkillsRequest(skill_ids=["7", "8"], user_id="owner", bot_id="bot"),
        entity_id=None,
        entity_type=None,
        bot_id=None,
        engine_type=None,
        ctx=SimpleNamespace(user_id="collaborator", bot_id="default"),
        bot_repo=_Bots(),
        control_plane=_ControlPlane(),
    )

    assert response.success is True
    assert response.data["success"] == [
        {"skill_id": "7", "name": "7"},
        {"skill_id": "8", "name": "8"},
    ]
    assert response.data["failed"] == []


@pytest.mark.asyncio
async def test_legacy_exact_set_routes_keep_the_target_owner_for_collaborators() -> None:
    class _ControlPlane:
        def __init__(self) -> None:
            self.operations: list[tuple[str, dict]] = []

        def resolve_legacy_set_scope(self, **_kwargs):
            raise AssertionError("an explicit bot_id must stay strictly scoped")

        def get_set(self, **kwargs):
            self.operations.append(("get_set", kwargs))
            return {
                "id": "set-1",
                "name": "tools",
                "description": None,
                "is_default": False,
                "is_builtin": False,
                "user_id": "owner",
                "gmt_created": "",
                "gmt_modified": "",
            }

        def update_set(self, **kwargs):
            self.operations.append(("update_set", kwargs))
            return {
                "id": "set-1",
                "name": "tools",
                "description": kwargs["description"],
                "is_default": False,
                "is_builtin": False,
                "user_id": "owner",
                "gmt_created": "",
                "gmt_modified": "",
            }

        def delete_set(self, **kwargs):
            self.operations.append(("delete_set", kwargs))

        def list_skills(self, **kwargs):
            self.operations.append(("list_skills", kwargs))
            return []

        async def remove_skills(self, **kwargs):
            self.operations.append(("remove_skills", kwargs))
            return [SkillSetSkillOutcome(skill_id="7", changed=True)]

        def list_mcps(self, **kwargs):
            self.operations.append(("list_mcps", kwargs))
            return []

    control_plane = _ControlPlane()
    common = {
        "entity_type": None,
        "engine_type": None,
        "ctx": SimpleNamespace(user_id="collaborator", bot_id="default"),
        "bot_repo": _Bots(),
        "control_plane": control_plane,
    }

    await get_skill_set(
        "set-1", user_id="owner", entity_id=None, bot_id="bot", **common
    )
    await update_skill_set(
        "set-1",
        UpdateSkillSetRequest(
            description="updated", user_id="owner", bot_id="bot"
        ),
        entity_id=None,
        bot_id=None,
        **common,
    )
    await inspect.unwrap(delete_skill_set)(
        "set-1", user_id="owner", entity_id=None, bot_id="bot", **common
    )
    await get_skill_set_skills(
        "set-1", user_id="owner", entity_id=None, bot_id="bot", **common
    )
    await inspect.unwrap(remove_skill_from_set)(
        "set-1",
        "7",
        user_id="owner",
        entity_id=None,
        bot_id="bot",
        **common,
    )
    await get_skill_set_mcps(
        "set-1",
        user_id="owner",
        entity_id=None,
        bot_id="bot",
        skill_set_service_factory=object(),
        **common,
    )

    assert [operation for operation, _kwargs in control_plane.operations] == [
        "get_set",
        "update_set",
        "delete_set",
        "list_skills",
        "remove_skills",
        "get_set",
        "list_mcps",
    ]
    for _operation, kwargs in control_plane.operations:
        assert kwargs["bot_id"] == "bot"
        assert kwargs["owner_id"] == "owner"
        assert kwargs["user_id"] == "collaborator"


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

        async def add_skills(self, **_kwargs):
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

        async def add_skills(self, **_kwargs):
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

        async def remove_skills(self, **kwargs):
            self.calls.append(("remove_skills", kwargs))
            return [SkillSetSkillOutcome(skill_id="7", changed=True)]

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
    assert (
        "remove_skills",
        {
            "bot_id": "persisted-bot",
            "owner_id": "owner",
            "user_id": "owner",
            "set_id": "set-1",
                "skill_ids": ["7"],
        },
    ) in control_plane.calls


@pytest.mark.asyncio
async def test_legacy_remove_keeps_the_historical_missing_member_404() -> None:
    from agentclaw.community.adapters.http.skill_center.skillsets import (
        remove_skill_from_set,
    )

    class _ControlPlane(_LegacySetScopeControlPlane):
        async def remove_skills(self, **_kwargs):
            return [SkillSetSkillOutcome(skill_id="7", changed=False)]

    with pytest.raises(SkillSetControlPlaneNotFoundError, match="Skill not found"):
        await remove_skill_from_set(
            "set-1",
            "7",
            user_id="owner",
            entity_id=None,
            entity_type=None,
            bot_id=None,
            engine_type=None,
            ctx=SimpleNamespace(user_id="owner", bot_id="default"),
            bot_repo=_AddressedBots(),
            control_plane=_ControlPlane(),
        )


@pytest.mark.asyncio
async def test_legacy_current_read_keeps_the_historical_id_key() -> None:
    """The deprecated wire's readers parse ``data.skill_set_id``; the
    re-sourced answer (first ordinary active Set) must keep the alias."""
    from agentclaw.community.adapters.http.skill_center.skills import (
        get_current_skill_set,
    )

    class _ControlPlane:
        def list_sets(self, **kwargs):
            assert kwargs["bot_id"] == "bot"
            return [
                {"id": "9", "name": "Default", "is_default": True, "is_active": True},
                {"id": "3", "name": "tools", "is_default": False, "is_active": True},
            ]

    response = await get_current_skill_set(
        entity_id="owner",
        entity_type=None,
        bot_id="bot",
        engine_type=None,
        ctx=SimpleNamespace(user_id="owner", bot_id="bot"),
        bot_repo=_Bots(),
        control_plane=_ControlPlane(),
    )

    assert response.success is True
    assert response.data["skill_set_id"] == "3"
    assert response.data["id"] == "3"
    assert response.data["name"] == "tools"
