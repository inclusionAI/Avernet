"""Authorization tests for URL and Node resource creation endpoints."""
from __future__ import annotations

from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


def _seed_owned_bot(world) -> None:
    make_staff_user(world, user_id="resource_owner")
    make_bot(
        world,
        bot_id="resource_bot",
        owner_id="resource_owner",
        bot_type="personal",
        status="ACTIVE",
    )


def _seed_other_users_bot(world) -> None:
    make_staff_user(world, user_id="resource_owner")
    make_staff_user(world, user_id="resource_attacker")
    make_bot(
        world,
        bot_id="resource_bot",
        owner_id="resource_owner",
        bot_type="personal",
        status="ACTIVE",
    )


@endpoint_test(
    method="POST",
    path="/api/resources/url",
    scenario="owner_can_create",
    input=CaseInput(
        headers={"x-user-id": "resource_owner"},
        query_params={"bot_id": "resource_bot"},
        json_body={"name": "Owner URL", "url": "https://example.com"},
    ),
    seed=_seed_owned_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"name": "Owner URL", "resource_type": "url"},
        },
    ),
)
def create_url_resource_owner_ok():
    """The Bot owner retains the existing URL resource creation flow."""


@endpoint_test(
    method="POST",
    path="/api/resources/url",
    scenario="other_user_forbidden",
    input=CaseInput(
        headers={"x-user-id": "resource_attacker"},
        query_params={
            "bot_id": "resource_bot",
            "owner_id": "resource_attacker",
        },
        json_body={"name": "Injected URL", "url": "https://attacker.example"},
    ),
    seed=_seed_other_users_bot,
    expect=ExpectError(status=403),
)
def create_url_resource_other_user_forbidden():
    """A caller cannot create a URL resource for another user's Bot."""


@endpoint_test(
    method="POST",
    path="/api/resources/node",
    scenario="owner_can_create",
    input=CaseInput(
        headers={"x-user-id": "resource_owner"},
        query_params={"bot_id": "resource_bot"},
        json_body={"name": "Owner Node", "node_address": "ipfs://owner-node"},
    ),
    seed=_seed_owned_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"name": "Owner Node", "resource_type": "node"},
        },
    ),
)
def create_node_resource_owner_ok():
    """The Bot owner retains the existing Node resource creation flow."""


@endpoint_test(
    method="POST",
    path="/api/resources/node",
    scenario="other_user_forbidden",
    input=CaseInput(
        headers={"x-user-id": "resource_attacker"},
        query_params={
            "bot_id": "resource_bot",
            "owner_id": "resource_attacker",
        },
        json_body={"name": "Injected Node", "node_address": "ipfs://attacker-node"},
    ),
    seed=_seed_other_users_bot,
    expect=ExpectError(status=403),
)
def create_node_resource_other_user_forbidden():
    """A caller cannot create a Node resource for another user's Bot."""
