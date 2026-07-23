"""Endpoint coverage for frontend-only user-list eligibility queries."""

from __future__ import annotations

from agentclaw.community.core.user_list.models import EntityUserListModel
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env
from tests.community.factories.access import make_staff_user
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


_ENTITY_ID = "caller_rollout_user"
_OTHER_ENTITY_ID = "another_user"
_USER_LIST_TYPE = "caller_identity"
_CORRECTION_TARGET = "corrected_rollout_user"
_CORRECTION_ACTOR = "first_authenticated_corrector"
_SECOND_CORRECTION_ACTOR = "second_authenticated_corrector"


def _seed_user(world) -> None:
    make_staff_user(world, user_id=_ENTITY_ID)


def _seed_current_env_member(world) -> None:
    _seed_user(world)
    with world.get(DatabasePlugin).orm_session() as session:
        session.add(
            EntityUserListModel(
                entity_id=_ENTITY_ID,
                user_list_type=_USER_LIST_TYPE,
                env=get_current_env(),
            )
        )


def _seed_other_entity_member(world) -> None:
    _seed_user(world)
    with world.get(DatabasePlugin).orm_session() as session:
        session.add(
            EntityUserListModel(
                entity_id=_OTHER_ENTITY_ID,
                user_list_type=_USER_LIST_TYPE,
                env=get_current_env(),
            )
        )


def _seed_other_scope_members(world) -> None:
    _seed_user(world)
    current_env = get_current_env()
    other_env = "prod" if current_env != "prod" else "dev"
    with world.get(DatabasePlugin).orm_session() as session:
        session.add_all(
            [
                EntityUserListModel(
                    entity_id=_ENTITY_ID,
                    user_list_type="another_feature",
                    env=current_env,
                ),
                EntityUserListModel(
                    entity_id=_ENTITY_ID,
                    user_list_type=_USER_LIST_TYPE,
                    env=other_env,
                ),
            ]
        )


def _seed_manual_env_member(world) -> None:
    _seed_user(world)
    with world.get(DatabasePlugin).orm_session() as session:
        session.add(
            EntityUserListModel(
                entity_id=_ENTITY_ID,
                user_list_type=_USER_LIST_TYPE,
                env="prod",
            )
        )


def _seed_correction_actor(world) -> None:
    make_staff_user(world, user_id=_CORRECTION_ACTOR)


def _seed_manual_correction_actor(world) -> None:
    make_staff_user(world, user_id=_CORRECTION_ACTOR)


def _seed_second_correction_actor_with_member(world) -> None:
    make_staff_user(world, user_id=_SECOND_CORRECTION_ACTOR)
    with world.get(DatabasePlugin).orm_session() as session:
        session.add(
            EntityUserListModel(
                entity_id=_CORRECTION_TARGET,
                user_list_type=_USER_LIST_TYPE,
                env=get_current_env(),
            )
        )


def _seed_non_correction_user(world) -> None:
    make_staff_user(world, user_id=_ENTITY_ID)


def _assert_ctoken_not_exposed(response, world) -> None:
    del world
    assert "opaque-gateway-compatibility-value" not in response.text


@endpoint_test(
    method="GET",
    path="/api/v1/user-lists/check",
    scenario="not_listed",
    input=CaseInput(
        query_params={
            "entity_id": _ENTITY_ID,
            "user_list_type": _USER_LIST_TYPE,
            "ctoken": "opaque-gateway-compatibility-value",
        },
        headers={"x-user-id": _ENTITY_ID},
    ),
    seed=_seed_user,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"in_whitelist": False},
        },
    ),
)
def user_list_check_returns_false_when_no_current_env_entry_exists():
    """A missing current-environment row must safely hide the frontend entry."""


@endpoint_test(
    method="GET",
    path="/api/v1/user-lists/check",
    scenario="listed_in_current_env",
    input=CaseInput(
        query_params={
            "entity_id": _ENTITY_ID,
            "user_list_type": _USER_LIST_TYPE,
            "ctoken": "opaque-gateway-compatibility-value",
        },
        headers={"x-user-id": _ENTITY_ID},
    ),
    seed=_seed_current_env_member,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"in_whitelist": True},
        },
    ),
    extra_assertions=(_assert_ctoken_not_exposed,),
)
def user_list_check_returns_true_for_an_exact_current_env_member():
    """Only a current-environment exact membership enables the frontend entry."""


@endpoint_test(
    method="GET",
    path="/api/v1/user-lists/check",
    scenario="different_type_or_env_is_not_listed",
    input=CaseInput(
        query_params={
            "entity_id": _ENTITY_ID,
            "user_list_type": _USER_LIST_TYPE,
        },
        headers={"x-user-id": _ENTITY_ID},
    ),
    seed=_seed_other_scope_members,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"in_whitelist": False},
        },
    ),
)
def user_list_check_ignores_other_types_and_environments():
    """Membership is isolated by both environment and user-list type."""


@endpoint_test(
    method="GET",
    path="/api/v1/user-lists/check",
    scenario="manual_env_override",
    input=CaseInput(
        query_params={
            "entity_id": _ENTITY_ID,
            "user_list_type": _USER_LIST_TYPE,
            "env": "prod",
        },
        headers={"x-user-id": _ENTITY_ID},
    ),
    seed=_seed_manual_env_member,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"in_whitelist": True},
        },
    ),
)
def user_list_check_supports_manual_environment_override():
    """An explicit env selects that environment instead of runtime env."""


@endpoint_test(
    method="GET",
    path="/api/v1/user-lists/check",
    scenario="rejects_invalid_env",
    input=CaseInput(
        query_params={
            "entity_id": _ENTITY_ID,
            "user_list_type": _USER_LIST_TYPE,
            "env": "staging",
        },
        headers={"x-user-id": _ENTITY_ID},
    ),
    seed=_seed_user,
    expect=ExpectError(status=422),
)
def user_list_check_rejects_invalid_environment():
    """Only normalized dev/pre/prod environment values are accepted."""


@endpoint_test(
    method="GET",
    path="/api/v1/user-lists/check",
    scenario="queries_other_entity",
    input=CaseInput(
        query_params={
            "entity_id": _OTHER_ENTITY_ID,
            "user_list_type": _USER_LIST_TYPE,
        },
        headers={"x-user-id": _ENTITY_ID},
    ),
    seed=_seed_other_entity_member,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"in_whitelist": True},
        },
    ),
)
def user_list_check_queries_the_requested_entity_membership():
    """The lookup uses the requested entity and keeps current-env isolation."""


@endpoint_test(
    method="GET",
    path="/api/v1/user-lists/check",
    scenario="rejects_invalid_user_list_type",
    input=CaseInput(
        query_params={
            "entity_id": _ENTITY_ID,
            "user_list_type": "caller identity",
        },
        headers={"x-user-id": _ENTITY_ID},
    ),
    seed=_seed_user,
    expect=ExpectError(status=422),
)
def user_list_check_rejects_an_invalid_user_list_type():
    """Feature-list identifiers are constrained to safe, stable tokens."""


@endpoint_test(
    method="GET",
    path="/api/v1/user-lists/check",
    scenario="requires_entity_id",
    input=CaseInput(
        query_params={"user_list_type": _USER_LIST_TYPE},
        headers={"x-user-id": _ENTITY_ID},
    ),
    seed=_seed_user,
    expect=ExpectError(status=422),
)
def user_list_check_requires_an_entity_id():
    """The lookup scope still requires an explicit entity identifier."""


@endpoint_test(
    method="PUT",
    path="/api/v1/user-lists/correct",
    scenario="authenticated_user_adds_member",
    input=CaseInput(
        query_params={"ctoken": "opaque-gateway-compatibility-value"},
        headers={"x-user-id": _CORRECTION_ACTOR},
        json_body={
            "entity_id": _CORRECTION_TARGET,
            "user_list_type": _USER_LIST_TYPE,
            "in_whitelist": True,
        },
    ),
    seed=_seed_correction_actor,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"in_whitelist": True}},
    ),
    extra_assertions=(_assert_ctoken_not_exposed,),
)
def user_list_correction_allows_an_authenticated_user_to_add():
    """An authenticated user may enable a current-environment entry."""


@endpoint_test(
    method="PUT",
    path="/api/v1/user-lists/correct",
    scenario="authenticated_user_removes_member",
    input=CaseInput(
        headers={"x-user-id": _SECOND_CORRECTION_ACTOR},
        json_body={
            "entity_id": _CORRECTION_TARGET,
            "user_list_type": _USER_LIST_TYPE,
            "in_whitelist": False,
        },
    ),
    seed=_seed_second_correction_actor_with_member,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"in_whitelist": False}},
    ),
)
def user_list_correction_allows_another_authenticated_user_to_remove():
    """Another authenticated user may disable an exact entry."""


@endpoint_test(
    method="PUT",
    path="/api/v1/user-lists/correct",
    scenario="authenticated_user_adds_member_in_manual_env",
    input=CaseInput(
        query_params={"env": "prod"},
        headers={"x-user-id": _CORRECTION_ACTOR},
        json_body={
            "entity_id": _CORRECTION_TARGET,
            "user_list_type": _USER_LIST_TYPE,
            "in_whitelist": True,
        },
    ),
    seed=_seed_manual_correction_actor,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"in_whitelist": True}},
    ),
)
def user_list_correction_supports_manual_environment_override():
    """An explicit env selects the membership partition being corrected."""


@endpoint_test(
    method="PUT",
    path="/api/v1/user-lists/correct",
    scenario="rejects_invalid_correction_env",
    input=CaseInput(
        query_params={"env": "staging"},
        headers={"x-user-id": _CORRECTION_ACTOR},
        json_body={
            "entity_id": _CORRECTION_TARGET,
            "user_list_type": _USER_LIST_TYPE,
            "in_whitelist": True,
        },
    ),
    seed=_seed_correction_actor,
    expect=ExpectError(status=422),
)
def user_list_correction_rejects_invalid_environment():
    """Correction env accepts only normalized dev/pre/prod values."""


@endpoint_test(
    method="PUT",
    path="/api/v1/user-lists/correct",
    scenario="any_authenticated_user_adds_member",
    input=CaseInput(
        headers={"x-user-id": _ENTITY_ID},
        json_body={
            "entity_id": _CORRECTION_TARGET,
            "user_list_type": _USER_LIST_TYPE,
            "in_whitelist": True,
        },
    ),
    seed=_seed_non_correction_user,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"in_whitelist": True}},
    ),
)
def user_list_correction_allows_every_authenticated_user():
    """The correction endpoint has no user-number-specific authorization."""


@endpoint_test(
    method="PUT",
    path="/api/v1/user-lists/correct",
    scenario="rejects_extra_body_field",
    input=CaseInput(
        headers={"x-user-id": _CORRECTION_ACTOR},
        json_body={
            "entity_id": _CORRECTION_TARGET,
            "user_list_type": _USER_LIST_TYPE,
            "in_whitelist": True,
            "unexpected": "rejected",
        },
    ),
    seed=_seed_correction_actor,
    expect=ExpectError(status=422),
)
def user_list_correction_rejects_extra_request_fields():
    """The correction write schema remains strict."""
