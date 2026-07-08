"""Endpoint coverage for SkillSet resource and CLI scope APIs."""
from __future__ import annotations

from unittest.mock import MagicMock

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.skill_center.services.repositories import SkillSetRepository
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.utils.env_utils import get_current_env
from tests.community.factories.access import make_staff_user
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test


_SKILL_SET_ID = "1"
_QUERY = {
    "entity_id": "u_skillset_cli",
    "entity_type": "staff",
    "bot_id": "bot_skillset_cli",
}
_HEADERS = {"x-user-id": "u_skillset_cli"}


def _bind_deps(world, *, default_set: bool = True) -> MagicMock:
    make_staff_user(world, user_id="u_skillset_cli")
    world.get(BotRepository).insert({
        "bot_id": "bot_skillset_cli",
        "bot_name": "SkillSet CLI Bot",
        "owner_id": "u_skillset_cli",
        "owner_name": "u_skillset_cli",
        "bot_type": "service",
        "status": "ACTIVE",
        "entity_id": "u_skillset_cli",
        "entity_type": "staff",
        "creator_id": "u_skillset_cli",
        "active_engine": "openclaw",
    })
    repo = world.get(SkillSetRepository)
    skill_set = repo.create({
        "name": "Default",
        "description": "Default set",
        "user_id": "u_skillset_cli",
        "bolt_id": "bot_skillset_cli",
        "is_default": default_set,
        "is_builtin": False,
        "is_active": 1,
        "engine_type": "openclaw",
    })
    assert skill_set["id"] == _SKILL_SET_ID
    repo.add_mcp_to_set(
        skill_set["id"],
        "web-search",
        "Web Search",
        description="",
        icon="",
        user_id="u_skillset_cli",
        env=get_current_env(),
    )

    passport = MagicMock(spec=PassportPlugin)
    passport.query_passport_clis.return_value = [
        {"cli_code": "cli.keep", "cli_name": "Keep CLI", "cli_desc": "kept"},
        {"cli_code": "cli.delete", "cli_name": "Delete CLI", "cli_desc": "removed"},
    ]
    world.injector.binder.bind(PassportPlugin, to=passport, scope=None)
    return passport


def _seed_resources_happy(world) -> None:
    _bind_deps(world)


def _seed_resources_cli_query_failure(world) -> None:
    passport = _bind_deps(world)
    passport.query_passport_clis.side_effect = RuntimeError("tcauth down")


def _seed_delete_cli_happy(world) -> None:
    _bind_deps(world)


def _seed_delete_cli_non_default(world) -> None:
    _bind_deps(world, default_set=False)


def _seed_delete_cli_not_found(world) -> None:
    _bind_deps(world)


def _seed_delete_cli_query_failure(world) -> None:
    passport = _bind_deps(world)
    passport.query_passport_clis.side_effect = RuntimeError("tcauth down")


def _assert_delete_updates_remaining_cli(response, world) -> None:
    passport = world.get(PassportPlugin)
    passport.update_passport.assert_called_once()
    kwargs = passport.update_passport.call_args.kwargs
    assert kwargs["bot_id"] == "bot_skillset_cli"
    assert kwargs["user_id"] == "u_skillset_cli"
    assert kwargs["resource_scope"]["cli_items"] == [
        {"cli_code": "cli.keep", "cli_name": "Keep CLI", "cli_desc": "kept"},
    ]
    mcp_codes = kwargs["resource_scope"]["mcp_codes"]
    assert mcp_codes
    assert all(isinstance(code, str) for code in mcp_codes)
    assert "hitl" not in mcp_codes


@endpoint_test(
    method="GET",
    path="/api/skillsets/resources",
    scenario="happy",
    input=CaseInput(query_params=_QUERY, headers=_HEADERS),
    seed=_seed_resources_happy,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": [
                {
                    "id": _SKILL_SET_ID,
                    "mcps": [{"server_code": "web-search"}],
                    "clis": [{"cli_code": "cli.keep"}],
                }
            ],
        },
    ),
)
def list_skillset_resources_happy():
    """Default capability set includes AgentPass CLI resources."""


@endpoint_test(
    method="GET",
    path="/api/skillsets/resources",
    scenario="cli_query_failure_degrades",
    input=CaseInput(query_params=_QUERY, headers=_HEADERS),
    seed=_seed_resources_cli_query_failure,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": [
                {
                    "id": _SKILL_SET_ID,
                    "mcps": [{"server_code": "web-search"}],
                    "clis": [],
                }
            ],
        },
    ),
)
def list_skillset_resources_degrades_when_cli_query_fails():
    """CLI query failures should not block Skill/MCP resource listing."""


@endpoint_test(
    method="GET",
    path="/api/skillsets/resources",
    scenario="missing_auth",
    input=CaseInput(query_params=_QUERY, headers={}),
    expect=ExpectError(status=401, json_contains={"detail": "Authentication required"}),
)
def list_skillset_resources_missing_auth():
    """Missing auth covers the resources endpoint error path."""


@endpoint_test(
    method="DELETE",
    path="/api/skillsets/{skill_set_id}/clis/{resource_code}",
    scenario="happy",
    input=CaseInput(
        path_params={"skill_set_id": _SKILL_SET_ID, "resource_code": "cli.delete"},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_delete_cli_happy,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_delete_updates_remaining_cli,),
)
def delete_skillset_cli_happy():
    """Deleting a CLI updates AgentPass with the remaining latest CLI scope."""


@endpoint_test(
    method="DELETE",
    path="/api/skillsets/{skill_set_id}/clis/{resource_code}",
    scenario="non_default_set",
    input=CaseInput(
        path_params={"skill_set_id": _SKILL_SET_ID, "resource_code": "cli.delete"},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_delete_cli_non_default,
    expect=ExpectError(
        status=400,
        json_contains={"detail": "CLI can only be removed from the default skill set"},
    ),
)
def delete_skillset_cli_rejects_non_default_set():
    """CLI removal is only exposed on the default capability set."""


@endpoint_test(
    method="DELETE",
    path="/api/skillsets/{skill_set_id}/clis/{resource_code}",
    scenario="cli_not_found",
    input=CaseInput(
        path_params={"skill_set_id": _SKILL_SET_ID, "resource_code": "cli.missing"},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_delete_cli_not_found,
    expect=ExpectError(
        status=404,
        json_contains={"detail": "CLI not found in passport scope"},
    ),
)
def delete_skillset_cli_rejects_missing_cli():
    """Deleting an unknown CLI does not submit an update to AgentPass."""


@endpoint_test(
    method="DELETE",
    path="/api/skillsets/{skill_set_id}/clis/{resource_code}",
    scenario="query_failure",
    input=CaseInput(
        path_params={"skill_set_id": _SKILL_SET_ID, "resource_code": "cli.delete"},
        query_params=_QUERY,
        headers=_HEADERS,
    ),
    seed=_seed_delete_cli_query_failure,
    expect=ExpectError(
        status=500,
        json_contains={"detail": "Failed to query CLI scope"},
    ),
)
def delete_skillset_cli_reports_query_failure():
    """CLI deletion reports query failures because it must update from the latest scope."""
