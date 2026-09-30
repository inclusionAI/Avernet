"""Endpoint coverage for bot-common-config management APIs."""
from __future__ import annotations

from agentclaw.community.core.common_config.bot_config_service import (
    BotCommonConfigService,
)
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test

_USER_HEADER = {"x-user-id": "u_bot_common_config_test"}
KEY = {"bot_id": "b_ep", "entity_id": "staff_ep", "config_key": "storage_policy"}


def _seed_policy(world, *, bot_id: str = "b_ep") -> None:
    world.get(BotCommonConfigService).upsert_record(
        bot_id=bot_id,
        entity_id="staff_ep",
        env="dev",
        config_key="storage_policy",
        value={"storage_type": "upfs", "source": "manual"},
    )


def _seed_for_update(world) -> None:
    world.get(BotCommonConfigService).create_record(
        **KEY, env="dev", value={"storage_type": "upfs"}
    )


def _seed_for_delete(world) -> None:
    world.get(BotCommonConfigService).create_record(
        **{**KEY, "bot_id": "b_del"}, env="dev", value={"storage_type": "upfs"}
    )


def _assert_update_succeeded(response, world) -> None:
    assert response.json()["success"] is True
    service = world.get(BotCommonConfigService)
    assert service.get_config(**KEY, env="dev") == {"storage_type": "nas"}


def _assert_delete_succeeded(response, world) -> None:
    assert response.json()["success"] is True
    service = world.get(BotCommonConfigService)
    assert service.get_config(**{**KEY, "bot_id": "b_del"}, env="dev") is None


@endpoint_test(
    method="GET",
    path="/api/v1/bot-common-config/list",
    scenario="happy",
    input=CaseInput(query_params={"bot_id": "b_ep"}, headers=_USER_HEADER),
    seed=_seed_policy,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"total": 1, "items": [{"bot_id": "b_ep", "env": "dev"}]},
        },
    ),
)
def list_bot_common_configs_happy():
    """List bot config rows filtered by bot_id."""


@endpoint_test(
    method="GET",
    path="/api/v1/bot-common-config/list",
    scenario="error",
    input=CaseInput(query_params={"page_num": "bad"}, headers=_USER_HEADER),
    expect=ExpectError(status=422),
)
def list_bot_common_configs_error():
    """Non-integer page_num fails request validation."""


@endpoint_test(
    method="POST",
    path="/api/v1/bot-common-config/get",
    scenario="happy",
    input=CaseInput(json_body=KEY, headers=_USER_HEADER),
    seed=_seed_policy,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"config_value": {"storage_type": "upfs", "source": "manual"}},
        },
    ),
)
def get_bot_common_config_happy():
    """Get a bot config row by unique key."""


@endpoint_test(
    method="POST",
    path="/api/v1/bot-common-config/get",
    scenario="error",
    input=CaseInput(json_body={**KEY, "bot_id": "missing"}, headers=_USER_HEADER),
    expect=ExpectError(status=404),
)
def get_bot_common_config_error():
    """Missing row returns 404."""


@endpoint_test(
    method="POST",
    path="/api/v1/bot-common-config/create",
    scenario="happy",
    input=CaseInput(
        json_body={**KEY, "bot_id": "b_new", "config_value": {"storage_type": "upfs"}},
        headers=_USER_HEADER,
    ),
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def create_bot_common_config_happy():
    """Create a new bot config row."""


@endpoint_test(
    method="POST",
    path="/api/v1/bot-common-config/create",
    scenario="error",
    input=CaseInput(
        json_body={**KEY, "config_value": {"storage_type": "upfs"}},
        headers=_USER_HEADER,
    ),
    seed=_seed_policy,
    expect=ExpectError(status=400),
)
def create_bot_common_config_error():
    """Duplicate unique key returns 400."""


@endpoint_test(
    method="POST",
    path="/api/v1/bot-common-config/update",
    scenario="happy",
    input=CaseInput(
        json_body={"id": 1, "config_value": {"storage_type": "nas"}},
        headers=_USER_HEADER,
    ),
    seed=_seed_for_update,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_update_succeeded,),
)
def update_bot_common_config_happy():
    """Update config_value by id."""


@endpoint_test(
    method="POST",
    path="/api/v1/bot-common-config/update",
    scenario="error",
    input=CaseInput(
        json_body={"id": 404, "config_value": {"storage_type": "nas"}},
        headers=_USER_HEADER,
    ),
    expect=ExpectError(status=404),
)
def update_bot_common_config_error():
    """Updating a missing row returns 404."""


@endpoint_test(
    method="POST",
    path="/api/v1/bot-common-config/upsert",
    scenario="happy",
    input=CaseInput(
        json_body={**KEY, "config_value": {"storage_type": "upfs", "source": "manual"}},
        headers=_USER_HEADER,
    ),
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def upsert_bot_common_config_happy():
    """Upsert by unique key."""


@endpoint_test(
    method="POST",
    path="/api/v1/bot-common-config/upsert",
    scenario="error",
    input=CaseInput(
        json_body={"bot_id": "b_ep", "entity_id": "staff_ep"},
        headers=_USER_HEADER,
    ),
    expect=ExpectError(status=422),
)
def upsert_bot_common_config_error():
    """Missing config_key fails request validation."""


@endpoint_test(
    method="POST",
    path="/api/v1/bot-common-config/batch-upsert",
    scenario="happy",
    input=CaseInput(
        json_body={
            "items": [
                {**KEY, "bot_id": "b_b1", "config_value": {"storage_type": "upfs"}},
                {**KEY, "bot_id": "b_b2", "config_value": {"storage_type": "upfs"}},
            ]
        },
        headers=_USER_HEADER,
    ),
    expect=ExpectSuccess(
        status=200, json_contains={"success": True, "data": {"config_ids": [1, 2]}}
    ),
)
def batch_upsert_bot_common_configs_happy():
    """Batch upsert writes every item."""


@endpoint_test(
    method="POST",
    path="/api/v1/bot-common-config/batch-upsert",
    scenario="error",
    input=CaseInput(json_body={"items": []}, headers=_USER_HEADER),
    expect=ExpectError(status=422),
)
def batch_upsert_bot_common_configs_error():
    """Empty items list fails request validation."""


@endpoint_test(
    method="POST",
    path="/api/v1/bot-common-config/delete",
    scenario="happy",
    input=CaseInput(json_body={"id": 1}, headers=_USER_HEADER),
    seed=_seed_for_delete,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_delete_succeeded,),
)
def delete_bot_common_config_happy():
    """Soft-delete a bot config row by id."""


@endpoint_test(
    method="POST",
    path="/api/v1/bot-common-config/delete",
    scenario="error",
    input=CaseInput(json_body={"id": 404}, headers=_USER_HEADER),
    expect=ExpectError(status=404),
)
def delete_bot_common_config_error():
    """Deleting a missing row returns 404."""
