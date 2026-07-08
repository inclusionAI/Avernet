"""Gateway 契约测试共享 fixtures。

提供:
- gw_client: 带认证 override 的 TestClient
- mock_user: 模拟用户
- assert_has_fields / assert_response_schema / assert_success: 响应断言工具
- assert_no_extra_fields: 严格字段检查（检测新增未声明字段）
- bind_mock_service: DI mock 绑定工具
"""
from __future__ import annotations

from typing import TypeAlias

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from injector import Module, singleton

from agentclaw.community.adapters.http.auth.dependencies import get_current_user, require_operator
from agentclaw.community.adapters.http.dependencies import get_request_context, RequestContext
from agentclaw.community.core.auth import AuthenticatedIdentity
from tests.community.contracts.gateway.schema_utils import (
    validate_api_response_against_snapshot,
    validate_data_against_snapshot,
)

# ── 字段类型别名 ───────────────────────────────────────────────────────────────

FieldType: TypeAlias = type | tuple[type, ...]
FieldSpec: TypeAlias = dict[str, FieldType]

# ── 模拟用户 ──────────────────────────────────────────────────────────────────

MOCK_USER = AuthenticatedIdentity(
    id="test-gw-user-id",
    operatorName="test_operator",
    outUserNo="448524",      # staffId alias
    nickName="TestUser",
    realName="测试用户",
)

MOCK_REQUEST_CTX = RequestContext(
    user_id="448524",
    bot_id="default",
    nick_name="TestUser",
)


async def _mock_get_current_user():
    return MOCK_USER


async def _mock_require_operator():
    return MOCK_USER


async def _mock_get_request_context():
    return MOCK_REQUEST_CTX


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_user():
    return MOCK_USER


@pytest.fixture
def gw_client(app_with_testing_modules):
    """带认证 override 的 TestClient。

    注: app_with_testing_modules 来自 tests/framework/fixtures.py，
    它创建全新 in-memory SQLite + fresh DI injector。
    """
    app = app_with_testing_modules
    app.dependency_overrides[get_current_user] = _mock_get_current_user
    app.dependency_overrides[require_operator] = _mock_require_operator
    app.dependency_overrides[get_request_context] = _mock_get_request_context
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
def contract_snapshot_update(request):
    """Whether contract snapshots should be updated during this test run."""
    return request.config.getoption("--snapshot-update", default=False)


# ── DI Mock 绑定工具 ──────────────────────────────────────────────────────────


def _get_injector(app):
    """从 FastAPI app 中获取 injector，优先 app.state.injector，其次全局。"""
    injector = getattr(app.state, "injector", None)
    if injector is not None:
        return injector
    from agentclaw.community.di import get_app_injector
    return get_app_injector()


def bind_mock_service(service_cls: type, mock_instance: MagicMock, app):
    """将 mock 实例绑定到 app 的 DI injector，替换 service_cls。

    用法:
        mock_svc = MagicMock(spec=SomeService)
        mock_svc.some_method.return_value = {...}
        bind_mock_service(SomeService, mock_svc, app)
    """
    from injector import SingletonScope

    class _OverrideModule(Module):
        def configure(self, binder):
            binder.bind(service_cls, to=mock_instance, scope=singleton)

    injector = _get_injector(app)
    injector.binder.install(_OverrideModule())

    # Clear any cached singleton instance so next injector.get() returns the mock.
    # binder.bind() overwrites Binder._bindings, but SingletonScope._context
    # retains the old instance and short-circuits resolution.
    scope_binding, _ = injector.binder.get_binding(SingletonScope)
    scope_instance = scope_binding.provider.get(injector)
    scope_instance._context.pop(service_cls, None)


# ── 断言工具 ──────────────────────────────────────────────────────────────────


def assert_has_fields(data: dict, required_fields: FieldSpec, label: str = "", *, strict: bool = False) -> None:
    """断言 data 包含所有 required_fields，缺失或类型不匹配则报错。

    Args:
        data: 待检查的 dict
        required_fields: dict[str, type | tuple[type, ...]]，检查字段存在性 + 类型。
            单个类型: {"bot_id": str}
            联合类型: {"value": (int, float)}, {"name": (str, type(None))}
        label: 断言描述标签
        strict: 如果为 True，同时检查 data 中是否包含 required_fields 之外的
            未知字段（用于检测 API 响应新增了未声明的字段）。
    """
    missing = [f for f in required_fields if f not in data]
    assert not missing, (
        f"{label} missing fields: {missing}. "
        f"Got keys: {sorted(data.keys())}"
    )
    type_errors = []
    for field, expected_type in required_fields.items():
        value = data[field]
        types = expected_type if isinstance(expected_type, tuple) else (expected_type,)
        if not isinstance(value, types):
            type_names = "/".join(t.__name__ for t in types)
            type_errors.append(
                f"  {field}: expected {type_names}, got {type(value).__name__} ({value!r})"
            )
    assert not type_errors, (
        f"{label} field type mismatch:\n" + "\n".join(type_errors)
    )
    if strict:
        extra = [f for f in data if f not in required_fields]
        assert not extra, (
            f"{label} has unexpected extra fields: {extra}. "
            f"Expected only: {sorted(required_fields.keys())}"
        )


def assert_no_extra_fields(data: dict, allowed_fields: set[str] | FieldSpec, label: str = "") -> None:
    """断言 data 中不包含 allowed_fields 之外的未知字段。

    当 API 响应新增了前端未预期的字段时，此断言可以立即捕获。
    通常与 assert_has_fields 配合使用：

        assert_has_fields(data, {"id": int, "name": str}, label="...")
        assert_no_extra_fields(data, {"id", "name"}, label="...")

    或直接在 assert_has_fields 中使用 strict=True。

    Args:
        data: 待检查的 dict
        allowed_fields: 允许的字段名集合。可以是 set[str] 或 dict[str, ...]
            （如果是 dict，仅取 keys 作为允许字段集合）
        label: 断言描述标签
    """
    if isinstance(allowed_fields, dict):
        allowed_set = set(allowed_fields.keys())
    else:
        allowed_set = set(allowed_fields)

    extra = [f for f in data if f not in allowed_set]
    assert not extra, (
        f"{label} has unexpected extra fields: {extra}. "
        f"Allowed only: {sorted(allowed_set)}"
    )


def assert_response_schema(
    resp_json: dict,
    *,
    required_top: FieldSpec | None = None,
    data_fields: FieldSpec | None = None,
    data_item_fields: FieldSpec | None = None,
    data_is_list: bool = False,
    label: str = "",
    strict: bool = False,
) -> dict:
    """断言 API 响应结构符合文档规范。

    Args:
        resp_json: 接口返回的 JSON (已解析为 dict)
        required_top: 响应顶层必须包含的字段，dict[str, type|tuple]
        data_fields: data 是 dict 时必须包含的字段，dict[str, type|tuple]
        data_item_fields: data 是 list 时，每个 item 必须包含的字段，dict[str, type|tuple]
        data_is_list: data 是否应为 list
        label: 断言描述标签（用于报错定位）
        strict: 如果为 True，同时检查是否存在未声明的额外字段

    Returns:
        resp_json（方便链式调用）
    """
    if required_top:
        assert_has_fields(resp_json, required_top, label=f"{label} top-level", strict=strict)

    data = resp_json.get("data")

    if data_fields and isinstance(data, dict):
        assert_has_fields(data, data_fields, label=f"{label} data", strict=strict)

    if data_is_list:
        assert isinstance(data, list), (
            f"{label} data should be list, got {type(data).__name__}"
        )

    if data_item_fields and isinstance(data, list):
        for i, item in enumerate(data):
            assert isinstance(item, dict), (
                f"{label} data[{i}] should be dict, got {type(item).__name__}"
            )
            assert_has_fields(item, data_item_fields, label=f"{label} data[{i}]", strict=strict)

    return resp_json


def assert_success(resp_json: dict, label: str = "") -> dict:
    """断言 success=True，返回 resp_json。"""
    assert resp_json.get("success") is True, (
        f"{label} expected success=True, got {resp_json.get('success')}, "
        f"message={resp_json.get('message')}"
    )
    return resp_json


def assert_response_data_contract(
    resp_json: dict,
    snapshot_name: str,
    *,
    update: bool = False,
) -> None:
    """Validate the actual response ``data`` payload against a strict snapshot.

    This is for broad response models such as ``ApiResponse`` or
    ``ApiResponse[dict]`` where the Pydantic schema cannot express the real
    nested data shape.
    """
    assert "data" in resp_json, (
        f"{snapshot_name} response should contain top-level 'data'. "
        f"Got keys: {sorted(resp_json.keys())}"
    )
    validate_data_against_snapshot(snapshot_name, resp_json["data"], update=update)


def assert_api_response_contract(
    resp_json: dict,
    snapshot_name: str,
    *,
    update: bool = False,
) -> None:
    """Validate the complete API response envelope against a strict snapshot.

    This is intended for service-backed API contract tests where the goal is
    to catch drift in the handler-visible JSON, including top-level fields.
    """
    validate_api_response_against_snapshot(snapshot_name, resp_json, update=update)


# ── pytest 选项 ──────────────────────────────────────────────────────────────

