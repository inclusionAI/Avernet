"""Endpoint coverage for common-config management APIs."""
from __future__ import annotations

from agentclaw.community.core.common_config import CommonConfigService
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test


_USER_HEADER = {"x-user-id": "u_common_config_test"}


def _seed_config(world, *, business_code: str, param_code: str, enable: str = "1") -> int:
    service = world.get(CommonConfigService)
    return service.upsert_config(
        business_code=business_code,
        business_name="测试业务",
        param_code=param_code,
        param_name="测试配置",
        param_value={"seeded": True},
        enable=enable,
        ext_info={"source": "endpoint-test"},
        env="dev",
    )


def _seed_get_config(world) -> None:
    _seed_config(world, business_code="cc_get", param_code="p_get")


def _seed_value_config(world) -> None:
    _seed_config(world, business_code="cc_value", param_code="p_value")


def _seed_list_config(world) -> None:
    _seed_config(world, business_code="cc_list", param_code="p_list")


def _seed_duplicate_config(world) -> None:
    service = world.get(CommonConfigService)
    service.create_config(
        business_code="cc_create_dup",
        business_name="测试业务",
        param_code="p_dup",
        param_name="重复配置",
        param_value={"created": True},
        enable="1",
        ext_info=None,
        env="dev",
    )


def _seed_update_config(world) -> None:
    world.common_config_id = _seed_config(
        world, business_code="cc_update", param_code="p_update"
    )


def _seed_delete_config(world) -> None:
    world.common_config_id = _seed_config(
        world, business_code="cc_delete", param_code="p_delete"
    )


def _seed_enable_config(world) -> None:
    world.common_config_id = _seed_config(
        world, business_code="cc_enable", param_code="p_enable", enable="0"
    )


def _seed_disable_config(world) -> None:
    world.common_config_id = _seed_config(
        world, business_code="cc_disable", param_code="p_disable", enable="1"
    )


def _assert_update_request_uses_seeded_id(response, world) -> None:
    assert world.common_config_id > 0


def _assert_update_succeeded(response, world) -> None:
    assert response.json()["success"] is True


def _assert_delete_succeeded(response, world) -> None:
    assert response.json()["success"] is True


def _assert_enable_succeeded(response, world) -> None:
    service = world.get(CommonConfigService)
    data = service.get_config(
        business_code="cc_enable", param_code="p_enable", env="dev"
    )
    assert data is not None
    assert data["enable"] == "1"


def _assert_disable_succeeded(response, world) -> None:
    service = world.get(CommonConfigService)
    data = service.get_config(
        business_code="cc_disable", param_code="p_disable", env="dev"
    )
    assert data is not None
    assert data["enable"] == "0"


@endpoint_test(
    method="GET",
    path="/api/v1/common-config/list",
    scenario="happy",
    input=CaseInput(
        query_params={"business_code": "cc_list"}, headers=_USER_HEADER
    ),
    seed=_seed_list_config,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"total": 1, "items": [{"business_code": "cc_list"}]},
        },
    ),
)
def list_common_configs_happy():
    """List common config rows."""


@endpoint_test(
    method="GET",
    path="/api/v1/common-config/list",
    scenario="error",
    input=CaseInput(query_params={"page_num": "bad"}, headers=_USER_HEADER),
    expect=ExpectError(status=422),
)
def list_common_configs_error():
    """Invalid page parameters surface as an error."""


@endpoint_test(
    method="POST",
    path="/api/v1/common-config/get",
    scenario="happy",
    input=CaseInput(
        json_body={"business_code": "cc_get", "param_code": "p_get"},
        headers=_USER_HEADER,
    ),
    seed=_seed_get_config,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"business_code": "cc_get", "param_code": "p_get"},
        },
    ),
)
def get_common_config_happy():
    """Get a config by unique key."""


@endpoint_test(
    method="POST",
    path="/api/v1/common-config/get",
    scenario="error",
    input=CaseInput(
        json_body={"business_code": "missing", "param_code": "missing"},
        headers=_USER_HEADER,
    ),
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 40401},
    ),
)
def get_common_config_error():
    """Missing config returns an application error envelope."""


@endpoint_test(
    method="POST",
    path="/api/v1/common-config/value",
    scenario="happy",
    input=CaseInput(
        json_body={"business_code": "cc_value", "param_code": "p_value"},
        headers=_USER_HEADER,
    ),
    seed=_seed_value_config,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"business_code": "cc_value", "param_code": "p_value"},
        },
    ),
)
def get_common_config_value_happy():
    """Read a config value."""


@endpoint_test(
    method="POST",
    path="/api/v1/common-config/value",
    scenario="error",
    input=CaseInput(json_body={"business_code": "cc_value"}, headers=_USER_HEADER),
    expect=ExpectError(status=422),
)
def get_common_config_value_error():
    """Missing required param_code fails validation."""


@endpoint_test(
    method="POST",
    path="/api/v1/common-config/create",
    scenario="happy",
    input=CaseInput(
        json_body={
            "business_code": "cc_create",
            "business_name": "测试业务",
            "param_code": "p_create",
            "param_name": "创建配置",
            "param_value": {"created": True},
            "enable": "1",
            "ext_info": {"source": "endpoint-test"},
        },
        headers=_USER_HEADER,
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"config_id": 1}},
    ),
)
def create_common_config_happy():
    """Create a config row."""


@endpoint_test(
    method="POST",
    path="/api/v1/common-config/create",
    scenario="error",
    input=CaseInput(
        json_body={
            "business_code": "cc_create_dup",
            "business_name": "测试业务",
            "param_code": "p_dup",
            "param_name": "重复配置",
            "param_value": {"created": True},
            "enable": "2",
        },
        headers=_USER_HEADER,
    ),
    seed=_seed_duplicate_config,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 40001},
    ),
)
def create_common_config_error():
    """Invalid enable flag returns an application error envelope."""


@endpoint_test(
    method="POST",
    path="/api/v1/common-config/update",
    scenario="happy",
    input=CaseInput(
        json_body={"id": 1, "param_name": "更新配置", "param_value": {"updated": True}},
        headers=_USER_HEADER,
    ),
    seed=_seed_update_config,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_update_request_uses_seeded_id, _assert_update_succeeded),
)
def update_common_config_happy():
    """Update a config row."""


@endpoint_test(
    method="POST",
    path="/api/v1/common-config/update",
    scenario="error",
    input=CaseInput(
        json_body={"id": 999999, "param_name": "missing"},
        headers=_USER_HEADER,
    ),
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 40401},
    ),
)
def update_common_config_error():
    """Updating a missing row returns not-found envelope."""


@endpoint_test(
    method="POST",
    path="/api/v1/common-config/upsert",
    scenario="happy",
    input=CaseInput(
        json_body={
            "business_code": "cc_upsert",
            "business_name": "测试业务",
            "param_code": "p_upsert",
            "param_name": "保存配置",
            "param_value": {"saved": True},
            "enable": "1",
        },
        headers=_USER_HEADER,
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"config_id": 1}},
    ),
)
def upsert_common_config_happy():
    """Upsert a config row."""


@endpoint_test(
    method="POST",
    path="/api/v1/common-config/upsert",
    scenario="error",
    input=CaseInput(
        json_body={
            "business_code": "cc_upsert_err",
            "param_code": "p_upsert_err",
            "param_name": "保存配置",
            "param_value": {"saved": True},
            "enable": "bad",
        },
        headers=_USER_HEADER,
    ),
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 40001},
    ),
)
def upsert_common_config_error():
    """Invalid upsert enable flag returns an application error envelope."""


@endpoint_test(
    method="POST",
    path="/api/v1/common-config/delete",
    scenario="happy",
    input=CaseInput(json_body={"id": 1}, headers=_USER_HEADER),
    seed=_seed_delete_config,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_delete_succeeded,),
)
def delete_common_config_happy():
    """Delete a config row by id."""


@endpoint_test(
    method="POST",
    path="/api/v1/common-config/delete",
    scenario="error",
    input=CaseInput(json_body={"id": 999999}, headers=_USER_HEADER),
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 40401},
    ),
)
def delete_common_config_error():
    """Deleting a missing row returns not-found envelope."""


@endpoint_test(
    method="POST",
    path="/api/v1/common-config/enable",
    scenario="happy",
    input=CaseInput(json_body={"id": 1}, headers=_USER_HEADER),
    seed=_seed_enable_config,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_enable_succeeded,),
)
def enable_common_config_happy():
    """Enable a disabled config row."""


@endpoint_test(
    method="POST",
    path="/api/v1/common-config/enable",
    scenario="error",
    input=CaseInput(json_body={"id": 999999}, headers=_USER_HEADER),
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 40401},
    ),
)
def enable_common_config_error():
    """Enabling a missing row returns not-found envelope."""


@endpoint_test(
    method="POST",
    path="/api/v1/common-config/disable",
    scenario="happy",
    input=CaseInput(json_body={"id": 1}, headers=_USER_HEADER),
    seed=_seed_disable_config,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_disable_succeeded,),
)
def disable_common_config_happy():
    """Disable an enabled config row."""


@endpoint_test(
    method="POST",
    path="/api/v1/common-config/disable",
    scenario="error",
    input=CaseInput(json_body={"id": 999999}, headers=_USER_HEADER),
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 40401},
    ),
)
def disable_common_config_error():
    """Disabling a missing row returns not-found envelope."""
