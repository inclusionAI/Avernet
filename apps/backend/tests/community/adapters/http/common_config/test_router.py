"""Common Config HTTP router unit tests."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.common_config import router
from agentclaw.community.adapters.http.common_config.schemas import (
    CreateCommonConfigRequest,
    DeleteCommonConfigRequest,
    GetCommonConfigRequest,
    GetCommonConfigValueRequest,
    ToggleCommonConfigRequest,
    UpdateCommonConfigRequest,
)


_USER = AuthenticatedUser(id="owner-1", staffId="owner-1", operatorName="owner-1")


def _run(coro):
    """Run async route handler from sync tests."""
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _fixed_env(monkeypatch):
    """Keep router env parameter deterministic."""
    monkeypatch.setattr(router.env_utils, "get_current_env", lambda: "pre")


def test_list_common_configs_passes_filters_and_returns_data():
    service = MagicMock()
    service.list_configs.return_value = {"items": [{"id": 1}], "total": 1}

    resp = _run(
        router.list_common_configs(
            business_code="biz",
            enable="1",
            keyword="white",
            page_num=2,
            page_size=20,
            user=_USER,
            service=service,
        )
    )

    assert resp.success is True
    assert resp.message == "OK"
    assert resp.error_code == 200
    assert resp.data == {"items": [{"id": 1}], "total": 1}
    service.list_configs.assert_called_once_with(
        env="pre",
        business_code="biz",
        enable="1",
        keyword="white",
        page_num=2,
        page_size=20,
    )


def test_get_common_config_returns_config_when_found():
    service = MagicMock()
    service.get_config.return_value = {"id": 1, "business_code": "biz"}

    resp = _run(
        router.get_common_config(
            GetCommonConfigRequest(
                business_code="biz",
                param_code="param",
                only_enabled=True,
            ),
            user=_USER,
            service=service,
        )
    )

    assert resp.success is True
    assert resp.message == "OK"
    assert resp.error_code == 200
    assert resp.data == {"id": 1, "business_code": "biz"}
    service.get_config.assert_called_once_with(
        business_code="biz",
        param_code="param",
        env="pre",
        only_enabled=True,
    )


def test_get_common_config_returns_404_when_missing():
    service = MagicMock()
    service.get_config.return_value = None

    resp = _run(
        router.get_common_config(
            GetCommonConfigRequest(business_code="biz", param_code="missing"),
            user=_USER,
            service=service,
        )
    )

    assert resp.success is False
    assert resp.message == "配置不存在"
    assert resp.error_code == 40401
    assert resp.data is None


def test_get_common_config_value_returns_value_payload():
    service = MagicMock()
    service.get_value.return_value = {"enable_all": True}

    resp = _run(
        router.get_common_config_value(
            GetCommonConfigValueRequest(
                business_code="biz",
                param_code="white_list",
                default={},
                only_enabled=False,
            ),
            user=_USER,
            service=service,
        )
    )

    assert resp.success is True
    assert resp.error_code == 200
    assert resp.data == {
        "business_code": "biz",
        "param_code": "white_list",
        "value": {"enable_all": True},
    }
    service.get_value.assert_called_once_with(
        business_code="biz",
        param_code="white_list",
        env="pre",
        default={},
        only_enabled=False,
    )


def _create_request() -> CreateCommonConfigRequest:
    return CreateCommonConfigRequest(
        business_code="biz",
        business_name="业务",
        param_code="param",
        param_name="参数",
        param_value={"k": "v"},
        enable="1",
        ext_info={"owner": "ocb"},
    )


def test_create_common_config_returns_created_id():
    service = MagicMock()
    service.create_config.return_value = 123

    resp = _run(router.create_common_config(_create_request(), user=_USER, service=service))

    assert resp.success is True
    assert resp.message == "配置已创建"
    assert resp.error_code == 200
    assert resp.data == {"config_id": 123}
    service.create_config.assert_called_once_with(
        business_code="biz",
        business_name="业务",
        param_code="param",
        param_name="参数",
        param_value={"k": "v"},
        enable="1",
        ext_info={"owner": "ocb"},
        env="pre",
    )


def test_create_common_config_returns_400_on_value_error():
    service = MagicMock()
    service.create_config.side_effect = ValueError("duplicate config")

    resp = _run(router.create_common_config(_create_request(), user=_USER, service=service))

    assert resp.success is False
    assert resp.message == "duplicate config"
    assert resp.error_code == 40001
    assert resp.data is None


def test_update_common_config_returns_success_and_only_set_fields():
    service = MagicMock()
    service.update_config.return_value = True
    req = UpdateCommonConfigRequest(id=123, param_name="新参数", enable="0")

    resp = _run(router.update_common_config(req, user=_USER, service=service))

    assert resp.success is True
    assert resp.message == "配置已更新"
    assert resp.error_code == 200
    assert resp.data is None
    service.update_config.assert_called_once_with(
        config_id=123,
        updates={"param_name": "新参数", "enable": "0"},
    )


def test_update_common_config_returns_400_on_value_error():
    service = MagicMock()
    service.update_config.side_effect = ValueError("invalid update")
    req = UpdateCommonConfigRequest(id=123, business_name="业务")

    resp = _run(router.update_common_config(req, user=_USER, service=service))

    assert resp.success is False
    assert resp.message == "invalid update"
    assert resp.error_code == 40001
    assert resp.data is None


def test_update_common_config_returns_404_when_service_returns_false():
    service = MagicMock()
    service.update_config.return_value = False
    req = UpdateCommonConfigRequest(id=404, ext_info={"x": 1})

    resp = _run(router.update_common_config(req, user=_USER, service=service))

    assert resp.success is False
    assert resp.message == "配置不存在或无可更新字段"
    assert resp.error_code == 40401
    assert resp.data is None


def test_upsert_common_config_returns_saved_id():
    service = MagicMock()
    service.upsert_config.return_value = 456

    resp = _run(router.upsert_common_config(_create_request(), user=_USER, service=service))

    assert resp.success is True
    assert resp.message == "配置已保存"
    assert resp.error_code == 200
    assert resp.data == {"config_id": 456}
    service.upsert_config.assert_called_once_with(
        business_code="biz",
        business_name="业务",
        param_code="param",
        param_name="参数",
        param_value={"k": "v"},
        enable="1",
        ext_info={"owner": "ocb"},
        env="pre",
    )


def test_upsert_common_config_returns_400_on_value_error():
    service = MagicMock()
    service.upsert_config.side_effect = ValueError("bad payload")

    resp = _run(router.upsert_common_config(_create_request(), user=_USER, service=service))

    assert resp.success is False
    assert resp.message == "bad payload"
    assert resp.error_code == 40001
    assert resp.data is None


def test_delete_common_config_by_id_uses_no_env_and_returns_success():
    service = MagicMock()
    service.delete_config.return_value = True

    resp = _run(
        router.delete_common_config(
            DeleteCommonConfigRequest(id=123),
            user=_USER,
            service=service,
        )
    )

    assert resp.success is True
    assert resp.message == "配置已删除"
    assert resp.error_code == 200
    assert resp.data is None
    service.delete_config.assert_called_once_with(
        config_id=123,
        business_code=None,
        param_code=None,
        env=None,
    )


def test_delete_common_config_by_unique_key_uses_current_env():
    service = MagicMock()
    service.delete_config.return_value = True

    resp = _run(
        router.delete_common_config(
            DeleteCommonConfigRequest(business_code="biz", param_code="param"),
            user=_USER,
            service=service,
        )
    )

    assert resp.success is True
    assert resp.error_code == 200
    service.delete_config.assert_called_once_with(
        config_id=None,
        business_code="biz",
        param_code="param",
        env="pre",
    )


def test_delete_common_config_returns_400_on_value_error():
    service = MagicMock()
    service.delete_config.side_effect = ValueError("id or unique key required")

    resp = _run(
        router.delete_common_config(
            DeleteCommonConfigRequest(),
            user=_USER,
            service=service,
        )
    )

    assert resp.success is False
    assert resp.message == "id or unique key required"
    assert resp.error_code == 40001
    assert resp.data is None


def test_delete_common_config_returns_404_when_missing():
    service = MagicMock()
    service.delete_config.return_value = False

    resp = _run(
        router.delete_common_config(
            DeleteCommonConfigRequest(id=404),
            user=_USER,
            service=service,
        )
    )

    assert resp.success is False
    assert resp.message == "配置不存在"
    assert resp.error_code == 40401
    assert resp.data is None


def test_enable_common_config_returns_success():
    service = MagicMock()
    service.enable_config.return_value = True

    resp = _run(
        router.enable_common_config(
            ToggleCommonConfigRequest(id=123),
            user=_USER,
            service=service,
        )
    )

    assert resp.success is True
    assert resp.message == "配置已启用"
    assert resp.error_code == 200
    assert resp.data is None
    service.enable_config.assert_called_once_with(config_id=123)


def test_enable_common_config_returns_404_when_missing():
    service = MagicMock()
    service.enable_config.return_value = False

    resp = _run(
        router.enable_common_config(
            ToggleCommonConfigRequest(id=404),
            user=_USER,
            service=service,
        )
    )

    assert resp.success is False
    assert resp.message == "配置不存在"
    assert resp.error_code == 40401
    assert resp.data is None


def test_enable_common_config_returns_business_error_when_protected():
    service = MagicMock()
    service.enable_config.side_effect = ValueError(
        "skills_pool layout rollout must use its operator API"
    )

    resp = _run(
        router.enable_common_config(
            ToggleCommonConfigRequest(id=1),
            user=_USER,
            service=service,
        )
    )

    assert resp.success is False
    assert resp.message == "skills_pool layout rollout must use its operator API"
    assert resp.error_code == 40001
    assert resp.data is None


def test_disable_common_config_returns_success():
    service = MagicMock()
    service.disable_config.return_value = True

    resp = _run(
        router.disable_common_config(
            ToggleCommonConfigRequest(id=123),
            user=_USER,
            service=service,
        )
    )

    assert resp.success is True
    assert resp.message == "配置已禁用"
    assert resp.error_code == 200
    assert resp.data is None
    service.disable_config.assert_called_once_with(config_id=123)


def test_disable_common_config_returns_404_when_missing():
    service = MagicMock()
    service.disable_config.return_value = False

    resp = _run(
        router.disable_common_config(
            ToggleCommonConfigRequest(id=404),
            user=_USER,
            service=service,
        )
    )

    assert resp.success is False
    assert resp.message == "配置不存在"
    assert resp.error_code == 40401
    assert resp.data is None


def test_disable_common_config_returns_business_error_when_protected():
    service = MagicMock()
    service.disable_config.side_effect = ValueError(
        "skills_pool layout rollout must use its operator API"
    )

    resp = _run(
        router.disable_common_config(
            ToggleCommonConfigRequest(id=1),
            user=_USER,
            service=service,
        )
    )

    assert resp.success is False
    assert resp.message == "skills_pool layout rollout must use its operator API"
    assert resp.error_code == 40001
    assert resp.data is None
