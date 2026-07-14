"""Coverage tests for SandboxDeviceRouter and handlers."""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.core.service.health_check.sandbox._sandbox_device_router import (
    AcBindingSandboxHandler,
    BaasSandboxHandler,
    PaginatedResult,
    SandboxDeviceRouter,
    TableType,
    _extract_sandbox_info_from_binding,
    _extract_sandbox_info_from_device,
    _parse_device_props,
)

# ==================== _parse_device_props ====================


class TestParseDeviceProps:
    def test_none_returns_empty(self):
        assert _parse_device_props(None) == {}

    def test_string_json(self):
        assert _parse_device_props('{"a": 1}') == {"a": 1}

    def test_string_invalid_json(self):
        assert _parse_device_props("not json") == {}

    def test_dict_passthrough(self):
        d = {"x": 1}
        assert _parse_device_props(d) is d

    def test_non_dict_non_string(self):
        assert _parse_device_props(123) == {}
        assert _parse_device_props([]) == {}


# ==================== _extract_sandbox_info_from_binding ====================


class TestExtractFromBinding:
    def test_normal(self):
        record = MagicMock()
        record.id = 10
        record.status = "ACTIVE"
        record.device_props = '{"sandbox_id": "sb-1", "ttl_expiration_time": "2024-01-01", "ttl_expiration_timestamp": 1700000, "refresh_fail_count": 3}'
        info = _extract_sandbox_info_from_binding(record)
        assert info.table_id == 10
        assert info.table_type == TableType.AC_BINDING
        assert info.sandbox_id == "sb-1"
        assert info.ttl_expiration_time == "2024-01-01"
        assert info.ttl_expiration_timestamp == 1700000
        assert info.refresh_fail_count == 3
        assert info.status == "ACTIVE"

    def test_none_device_props(self):
        record = MagicMock()
        record.id = 1
        record.status = "ACTIVE"
        record.device_props = None
        info = _extract_sandbox_info_from_binding(record)
        assert info.sandbox_id is None
        assert info.refresh_fail_count == 0

    def test_dict_device_props(self):
        record = MagicMock()
        record.id = 2
        record.status = "STOPPED"
        record.device_props = {"sandbox_id": "sb-2"}
        info = _extract_sandbox_info_from_binding(record)
        assert info.sandbox_id == "sb-2"
        assert info.refresh_fail_count == 0


# ==================== _extract_sandbox_info_from_device ====================


class TestExtractFromDevice:
    def test_normal(self):
        record = MagicMock()
        record.id = 20
        record.status = "ACTIVE"
        record.provider_device_props = '{"sandbox_id": "sb-3", "refresh_fail_count": 5}'
        info = _extract_sandbox_info_from_device(record)
        assert info.table_id == 20
        assert info.table_type == TableType.BAAS
        assert info.sandbox_id == "sb-3"
        assert info.refresh_fail_count == 5

    def test_none_props(self):
        record = MagicMock()
        record.id = 3
        record.status = "ACTIVE"
        record.provider_device_props = None
        info = _extract_sandbox_info_from_device(record)
        assert info.sandbox_id is None
        assert info.refresh_fail_count == 0


# ==================== AcBindingSandboxHandler ====================


class TestAcBindingHandler:
    @pytest.fixture
    def handler(self):
        binding_repo = MagicMock()
        paas_facade = MagicMock()
        return AcBindingSandboxHandler(binding_repo, paas_facade)

    def test_query_active_sandboxes(self, handler):
        record = MagicMock()
        record.id = 1
        record.status = "ACTIVE"
        record.device_props = '{"sandbox_id": "sb-1"}'
        handler._binding_repo.list_bindings_by_providers.return_value = (1, [record])
        result = handler.query_active_sandboxes(env="prod", page=1, page_size=10)
        assert result.total == 1
        assert len(result.items) == 1
        assert result.items[0].sandbox_id == "sb-1"
        handler._binding_repo.list_bindings_by_providers.assert_called_once_with(
            providers=["arca", "baas"],
            env="prod",
            status="ACTIVE",
            page=1,
            page_size=10,
        )

    @pytest.mark.asyncio
    async def test_warn_device_not_found(self, handler):
        handler._binding_repo.get_by_id.return_value = None
        with pytest.raises(ValueError, match="Binding record not found"):
            await handler.warn_device(table_id=99)

    @pytest.mark.asyncio
    async def test_warn_device_not_active(self, handler):
        record = MagicMock()
        record.status = "STOPPED"
        handler._binding_repo.get_by_id.return_value = record
        result = await handler.warn_device(table_id=1)
        assert result.action == "SKIP"

    @pytest.mark.asyncio
    async def test_warn_device_no_sandbox_id(self, handler):
        record = MagicMock()
        record.status = "ACTIVE"
        record.device_props = "{}"
        handler._binding_repo.get_by_id.return_value = record
        result = await handler.warn_device(table_id=1)
        assert result.action == "SKIP"

    @pytest.mark.asyncio
    async def test_warn_device_exceeds_threshold(self, handler):
        record = MagicMock()
        record.status = "ACTIVE"
        record.device_props = '{"sandbox_id": "sb-1", "refresh_fail_count": 10}'
        handler._binding_repo.get_by_id.return_value = record
        result = await handler.warn_device(table_id=1)
        assert result.action == "STOPPED"
        assert result.refresh_fail_count == 10
        handler._binding_repo.update_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_warn_device_probe_success_with_ttl(self, handler):
        record = MagicMock()
        record.status = "ACTIVE"
        record.device_props = '{"sandbox_id": "sb-1", "refresh_fail_count": 2}'
        handler._binding_repo.get_by_id.return_value = record
        device_info = MagicMock()
        device_info.ttl_timestamp = 1700000000000
        handler._paas_facade.get_device_info = AsyncMock(return_value=device_info)
        result = await handler.warn_device(table_id=1)
        assert result.action == "RESET"
        assert result.refresh_fail_count == 0
        handler._binding_repo.update_device_props_ttl.assert_called_once()

    @pytest.mark.asyncio
    async def test_warn_device_probe_success_no_ttl(self, handler):
        record = MagicMock()
        record.status = "ACTIVE"
        record.device_props = '{"sandbox_id": "sb-1", "refresh_fail_count": 2}'
        handler._binding_repo.get_by_id.return_value = record
        device_info = MagicMock()
        device_info.ttl_timestamp = None
        handler._paas_facade.get_device_info = AsyncMock(return_value=device_info)
        result = await handler.warn_device(table_id=1)
        assert result.action == "RESET"
        handler._binding_repo.update_device_props_refresh_fail_count.assert_called_once()

    @pytest.mark.asyncio
    async def test_warn_device_probe_failure(self, handler):
        record = MagicMock()
        record.status = "ACTIVE"
        record.device_props = '{"sandbox_id": "sb-1", "refresh_fail_count": 3}'
        handler._binding_repo.get_by_id.return_value = record
        handler._paas_facade.get_device_info = AsyncMock(side_effect=Exception("fail"))
        result = await handler.warn_device(table_id=1)
        assert result.action == "INCREMENT"
        assert result.refresh_fail_count == 4

    @pytest.mark.asyncio
    async def test_warn_device_probe_success_no_ttl_attr(self, handler):
        record = MagicMock()
        record.status = "ACTIVE"
        record.device_props = '{"sandbox_id": "sb-1", "refresh_fail_count": 0}'
        handler._binding_repo.get_by_id.return_value = record
        device_info = MagicMock(spec=[])  # No attributes
        handler._paas_facade.get_device_info = AsyncMock(return_value=device_info)
        result = await handler.warn_device(table_id=1)
        assert result.action == "RESET"

    @pytest.mark.asyncio
    async def test_renew_ttl_not_found(self, handler):
        handler._binding_repo.get_by_id.return_value = None
        with pytest.raises(ValueError, match="Binding record not found"):
            await handler.renew_ttl(table_id=99)

    @pytest.mark.asyncio
    async def test_renew_ttl_no_sandbox_id(self, handler):
        record = MagicMock()
        record.device_props = "{}"
        handler._binding_repo.get_by_id.return_value = record
        with pytest.raises(ValueError, match="No sandbox_id"):
            await handler.renew_ttl(table_id=1)

    @pytest.mark.asyncio
    async def test_renew_ttl_success(self, handler):
        record = MagicMock()
        record.device_props = '{"sandbox_id": "sb-1", "ttl_expiration_time": "old"}'
        handler._binding_repo.get_by_id.return_value = record
        ttl_info = MagicMock()
        ttl_info.success = True
        ttl_info.new_expiration_time = datetime(2024, 6, 1, 12, 0, 0)
        handler._paas_facade.update_device_ttl = AsyncMock(return_value=ttl_info)
        result = await handler.renew_ttl(table_id=1)
        assert result.success is True
        assert result.old_expiration_time == "old"
        assert result.new_expiration_time == "2024-06-01 12:00:00"
        handler._binding_repo.update_device_props_ttl.assert_called_once()

    @pytest.mark.asyncio
    async def test_renew_ttl_failure_no_success(self, handler):
        record = MagicMock()
        record.device_props = '{"sandbox_id": "sb-1", "refresh_fail_count": 2}'
        handler._binding_repo.get_by_id.return_value = record
        ttl_info = MagicMock()
        ttl_info.success = False
        ttl_info.new_expiration_time = None
        ttl_info.error = "denied"
        handler._paas_facade.update_device_ttl = AsyncMock(return_value=ttl_info)
        result = await handler.renew_ttl(table_id=1)
        assert result.success is False
        assert result.error == "denied"

    @pytest.mark.asyncio
    async def test_renew_ttl_exception(self, handler):
        record = MagicMock()
        record.device_props = '{"sandbox_id": "sb-1", "refresh_fail_count": 1}'
        handler._binding_repo.get_by_id.return_value = record
        handler._paas_facade.update_device_ttl = AsyncMock(side_effect=Exception("err"))
        result = await handler.renew_ttl(table_id=1)
        assert result.success is False
        assert "err" in result.error


# ==================== BaasSandboxHandler ====================


class TestBaasHandler:
    @pytest.fixture
    def handler(self):
        binding_repo = MagicMock()
        paas_facade = MagicMock()
        return BaasSandboxHandler(binding_repo, paas_facade)

    def test_query_active_sandboxes(self, handler):
        row = {
            "id": 1,
            "status": "ACTIVE",
            "provider_device_props": '{"sandbox_id": "sb-1"}',
        }
        handler._binding_repo.list_baas_devices_active_paginated.return_value = (
            1,
            [row],
        )
        result = handler.query_active_sandboxes(env="prod", page=1, page_size=10)
        assert result.total == 1
        assert result.items[0].sandbox_id == "sb-1"

    def test_query_active_sandboxes_empty(self, handler):
        handler._binding_repo.list_baas_devices_active_paginated.return_value = (0, [])
        result = handler.query_active_sandboxes(env="prod", page=1, page_size=10)
        assert result.total == 0
        assert len(result.items) == 0

    @pytest.mark.asyncio
    async def test_warn_device_not_found(self, handler):
        handler._binding_repo.get_baas_device_by_id.return_value = None
        with pytest.raises(ValueError, match="Device record not found"):
            await handler.warn_device(table_id=99)

    @pytest.mark.asyncio
    async def test_warn_device_not_active(self, handler):
        handler._binding_repo.get_baas_device_by_id.return_value = {"status": "STOPPED"}
        result = await handler.warn_device(table_id=1)
        assert result.action == "SKIP"

    @pytest.mark.asyncio
    async def test_warn_device_exceeds_threshold(self, handler):
        handler._binding_repo.get_baas_device_by_id.return_value = {
            "status": "ACTIVE",
            "provider_device_props": '{"sandbox_id": "sb-1", "refresh_fail_count": 10}',
        }
        result = await handler.warn_device(table_id=1)
        assert result.action == "STOPPED"
        handler._binding_repo.update_baas_device_status_by_id.assert_called_once()

    @pytest.mark.asyncio
    async def test_warn_device_probe_success_with_ttl(self, handler):
        handler._binding_repo.get_baas_device_by_id.return_value = {
            "status": "ACTIVE",
            "provider_device_props": '{"sandbox_id": "sb-1", "refresh_fail_count": 2}',
        }
        device_info = MagicMock()
        device_info.ttl_timestamp = 1700000000000
        handler._paas_facade.get_device_info = AsyncMock(return_value=device_info)
        result = await handler.warn_device(table_id=1)
        assert result.action == "RESET"
        handler._binding_repo.update_baas_device_ttl_by_id.assert_called_once()

    @pytest.mark.asyncio
    async def test_warn_device_probe_success_no_ttl(self, handler):
        handler._binding_repo.get_baas_device_by_id.return_value = {
            "status": "ACTIVE",
            "provider_device_props": '{"sandbox_id": "sb-1", "refresh_fail_count": 0}',
        }
        device_info = MagicMock()
        device_info.ttl_timestamp = None
        handler._paas_facade.get_device_info = AsyncMock(return_value=device_info)
        result = await handler.warn_device(table_id=1)
        assert result.action == "RESET"
        handler._binding_repo.update_baas_device_refresh_fail_count_by_id.assert_called_once()

    @pytest.mark.asyncio
    async def test_warn_device_probe_failure(self, handler):
        handler._binding_repo.get_baas_device_by_id.return_value = {
            "status": "ACTIVE",
            "provider_device_props": '{"sandbox_id": "sb-1", "refresh_fail_count": 1}',
        }
        handler._paas_facade.get_device_info = AsyncMock(side_effect=Exception("boom"))
        result = await handler.warn_device(table_id=1)
        assert result.action == "INCREMENT"
        assert result.refresh_fail_count == 2

    @pytest.mark.asyncio
    async def test_renew_ttl_not_found(self, handler):
        handler._binding_repo.get_baas_device_by_id.return_value = None
        with pytest.raises(ValueError, match="Device record not found"):
            await handler.renew_ttl(table_id=99)

    @pytest.mark.asyncio
    async def test_renew_ttl_no_provider_device_id(self, handler):
        handler._binding_repo.get_baas_device_by_id.return_value = {
            "provider_device_id": None,
        }
        with pytest.raises(ValueError, match="No provider_device_id"):
            await handler.renew_ttl(table_id=1)

    @pytest.mark.asyncio
    async def test_renew_ttl_success(self, handler):
        handler._binding_repo.get_baas_device_by_id.return_value = {
            "provider_device_id": "sb-1",
            "provider_device_props": '{"ttl_expiration_time": "old"}',
        }
        ttl_info = MagicMock()
        ttl_info.success = True
        ttl_info.new_expiration_time = datetime(2024, 6, 1, 12, 0, 0)
        handler._paas_facade.update_device_ttl = AsyncMock(return_value=ttl_info)
        result = await handler.renew_ttl(table_id=1)
        assert result.success is True
        assert result.device_id == "sb-1"
        handler._binding_repo.update_baas_device_ttl_by_id.assert_called_once()

    @pytest.mark.asyncio
    async def test_renew_ttl_failure(self, handler):
        handler._binding_repo.get_baas_device_by_id.return_value = {
            "provider_device_id": "sb-1",
            "provider_device_props": '{"refresh_fail_count": 1}',
        }
        ttl_info = MagicMock()
        ttl_info.success = False
        ttl_info.new_expiration_time = None
        ttl_info.error = "no perm"
        handler._paas_facade.update_device_ttl = AsyncMock(return_value=ttl_info)
        result = await handler.renew_ttl(table_id=1)
        assert result.success is False
        assert result.error == "no perm"

    @pytest.mark.asyncio
    async def test_renew_ttl_exception(self, handler):
        handler._binding_repo.get_baas_device_by_id.return_value = {
            "provider_device_id": "sb-1",
            "provider_device_props": '{"refresh_fail_count": 1}',
        }
        handler._paas_facade.update_device_ttl = AsyncMock(side_effect=Exception("ex"))
        result = await handler.renew_ttl(table_id=1)
        assert result.success is False
        assert "ex" in result.error


# ==================== SandboxDeviceRouter ====================


class TestSandboxDeviceRouter:
    def test_init_missing_ac_binding(self):
        with pytest.raises(ValueError, match="Missing handler for"):
            SandboxDeviceRouter({TableType.BAAS: MagicMock()})

    def test_init_missing_baas(self):
        with pytest.raises(ValueError, match="Missing handler for"):
            SandboxDeviceRouter({TableType.AC_BINDING: MagicMock()})

    def test_init_success(self):
        h1, h2 = MagicMock(), MagicMock()
        router = SandboxDeviceRouter({TableType.AC_BINDING: h1, TableType.BAAS: h2})
        assert router._handlers[TableType.AC_BINDING] is h1

    def test_get_handler_invalid_type(self):
        h1, h2 = MagicMock(), MagicMock()
        router = SandboxDeviceRouter({TableType.AC_BINDING: h1, TableType.BAAS: h2})
        with pytest.raises(ValueError):
            router._get_handler("invalid_type")

    def test_query_active_sandboxes(self):
        h1 = MagicMock()
        h1.query_active_sandboxes.return_value = PaginatedResult(0, 1, 10, [])
        h2 = MagicMock()
        router = SandboxDeviceRouter({TableType.AC_BINDING: h1, TableType.BAAS: h2})
        result = router.query_active_sandboxes(
            env="prod", table_type="ac_binding", page=1, page_size=10
        )
        assert result.total == 0
        h1.query_active_sandboxes.assert_called_once()

    @pytest.mark.asyncio
    async def test_warn_device(self):
        h1 = MagicMock()
        h1.warn_device = AsyncMock(return_value=MagicMock())
        h2 = MagicMock()
        router = SandboxDeviceRouter({TableType.AC_BINDING: h1, TableType.BAAS: h2})
        await router.warn_device(table_id=1, table_type="ac_binding")
        h1.warn_device.assert_called_once_with(table_id=1)

    @pytest.mark.asyncio
    async def test_renew_ttl(self):
        h1 = MagicMock()
        h1.renew_ttl = AsyncMock(return_value=MagicMock())
        h2 = MagicMock()
        h2.renew_ttl = AsyncMock(return_value=MagicMock())
        router = SandboxDeviceRouter({TableType.AC_BINDING: h1, TableType.BAAS: h2})
        await router.renew_ttl(table_type="baas", table_id=2)
        h2.renew_ttl.assert_called_once_with(table_id=2)
