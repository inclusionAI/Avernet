"""Tests for BaasDeviceService.

Part 1: _resolve_bot_by_binding_id
Part 2: provider=baas lifecycle hooks (_setup_directory, _do_allocate,
        _do_allocate_nas, _start_service, _do_release, _query_device_info)
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.bot_management.token_vault import TokenVault
from agentclaw.community.core.devices.errors import DeviceExecShellError
from agentclaw.community.core.devices.models import AllocatedDevice, DeviceBindingStatus, OperatorContext
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.devices.services.baas_device_service import (
    BaasDeviceService,
    BaasDeviceServiceError,
)
from agentclaw.community.core.devices.services.baas_publish_task_handlers import (
    BAAS_CREATE_PUBLISH_POLL_TASK,
)
from agentclaw.community.core.devices.services.baas_template_resolver import (
    BaasTemplateResolution,
)
from agentclaw.community.core.devices.services.device_service import BAAS_DEVICE_PROVIDER
from agentclaw.community.core.service_bot.services.baas_service import BaasServiceError
from agentclaw.community.core.service_bot.services.deploy.provider_resolver import (
    TECLAW_DEVICE_PROVIDER,
)


# ===========================================================================
# Shared helpers
# ===========================================================================


def _make_binding_record(
    *,
    id: int = 1,
    entity_id: str = "u001",
    device_props: dict | None = None,
) -> DeviceBindingRecord:
    return DeviceBindingRecord(
        id=id,
        entity_id=entity_id,
        entity_type="staff",
        device_id="staff_u001_default",
        device_provider="baas",
        env="dev",
        device_props=device_props if device_props is not None else {},
        status="ACTIVE",
        apply_reason=None,
        applied_by=entity_id,
        release_reason=None,
        released_by=None,
        released_at=None,
        last_alive_at=None,
        gmt_create=datetime(2024, 1, 1),
        gmt_modified=datetime(2024, 1, 1),
    )


def _make_service(
    repo=None,
    bot_query=None,
    baas_service=None,
    template_resolver=None,
    vault=None,
    task_queue_service=None,
    template_service=None,
) -> BaasDeviceService:
    repo = repo or MagicMock()
    bot_query = bot_query or MagicMock()
    bs = baas_service or MagicMock()
    if not hasattr(bs, "_baas_api_base") or not isinstance(bs._baas_api_base, str):
        bs._baas_api_base = "http://baas.local"
    if template_resolver is None:
        template_resolver = MagicMock()
        template_resolver.resolve_template.return_value = BaasTemplateResolution(
            template_uid="default_template",
            template_uuid="TEMPLATE-test",
            source="test",
        )
    return BaasDeviceService(
        repository=repo,
        baas_service=bs,
        bot_query=bot_query,
        bot_sync=MagicMock(),
        oss_record_repo=MagicMock(),
        mcp_sync=MagicMock(),
        template_resolver=template_resolver,
        vault=vault,
        task_queue_service=task_queue_service,
        template_service=template_service,
    )


def _operator(staff_id: str = "u001") -> OperatorContext:
    return OperatorContext(
        staff_id=staff_id,
        staff=staff_id,
        nick_name="User",
        operator_name="User",
        tenant_id="default",
    )


class TestUpdateDeviceHeaders:
    def test_teclaw_uses_teclaw_authorization_rule_builder(self):
        baas = MagicMock()
        rule = MagicMock()
        baas._build_teclaw_outbound_operation_rule.return_value = rule
        baas.get_device_by_uuid.return_value = {"provider_device_id": "PAAS-1"}
        svc = _make_service(baas_service=baas)
        device = AllocatedDevice(
            device_id="BOT-teclaw",
            device_provider=TECLAW_DEVICE_PROVIDER,
            device_props={
                "bolt_id": "bot-1",
                "entity_id": "u1",
                "device_uuid": "DEVICE-1",
            },
        )

        result = svc.update_device_headers(
            device=device,
            agent_pass_token="passport-token",
            agent_code="agent-code",
        )

        baas._build_teclaw_outbound_operation_rule.assert_called_once_with(
            agent_pass_token="passport-token"
        )
        baas._build_outbound_operation_rule.assert_not_called()
        baas.update_device_outbound_rule.assert_called_once_with("PAAS-1", rule)
        assert result == [{"baas_device_uuid": "DEVICE-1", "paas_device_id": "PAAS-1"}]

    def test_teclaw_skips_update_when_rule_builder_returns_none(self):
        baas = MagicMock()
        baas._build_teclaw_outbound_operation_rule.return_value = None
        svc = _make_service(baas_service=baas)
        device = AllocatedDevice(
            device_id="BOT-teclaw",
            device_provider=TECLAW_DEVICE_PROVIDER,
            device_props={
                "bolt_id": "bot-1",
                "entity_id": "u1",
                "device_uuid": "DEVICE-1",
            },
        )

        result = svc.update_device_headers(device=device, agent_pass_token="")

        assert result == []
        baas._build_teclaw_outbound_operation_rule.assert_called_once_with(
            agent_pass_token=""
        )
        baas.update_device_outbound_rule.assert_not_called()

    def test_baas_provider_keeps_general_outbound_rule_builder(self):
        baas = MagicMock()
        rule = MagicMock()
        baas._build_outbound_operation_rule.return_value = rule
        baas.get_device_by_uuid.return_value = {"provider_device_id": "PAAS-1"}
        svc = _make_service(baas_service=baas)
        device = AllocatedDevice(
            device_id="BOT-baas",
            device_provider=BAAS_DEVICE_PROVIDER,
            device_props={
                "bolt_id": "bot-1",
                "entity_id": "u1",
                "device_uuid": "DEVICE-1",
            },
        )

        svc.update_device_headers(
            device=device,
            agent_pass_token="passport-token",
            agent_code="agent-code",
        )

        baas._build_outbound_operation_rule.assert_called_once_with(
            bot_id="bot-1",
            owner_id="u1",
            agent_pass_token="passport-token",
            agent_code="agent-code",
        )
        baas._build_teclaw_outbound_operation_rule.assert_not_called()
        baas.update_device_outbound_rule.assert_called_once_with("PAAS-1", rule)

    def test_build_rule_failure_is_wrapped(self):
        baas = MagicMock()
        baas._build_outbound_operation_rule.side_effect = RuntimeError("bad rule")
        svc = _make_service(baas_service=baas)
        device = AllocatedDevice(
            device_id="BOT-baas",
            device_provider=BAAS_DEVICE_PROVIDER,
            device_props={"bolt_id": "bot-1", "entity_id": "u1"},
        )

        with pytest.raises(BaasDeviceServiceError, match="outbound rule"):
            svc.update_device_headers(device=device)

        baas.update_device_outbound_rule.assert_not_called()

    def test_single_device_missing_provider_device_id_is_wrapped(self):
        baas = MagicMock()
        baas._build_outbound_operation_rule.return_value = MagicMock()
        baas.get_device_by_uuid.return_value = {}
        svc = _make_service(baas_service=baas)
        device = AllocatedDevice(
            device_id="BOT-baas",
            device_provider=BAAS_DEVICE_PROVIDER,
            device_props={
                "bolt_id": "bot-1",
                "entity_id": "u1",
                "device_uuid": "DEVICE-1",
            },
        )

        with pytest.raises(BaasDeviceServiceError, match="provider_device_id"):
            svc.update_device_headers(device=device)

    def test_batch_mode_uses_bot_uuid_and_skips_invalid_devices(self):
        baas = MagicMock()
        rule = MagicMock()
        baas._build_outbound_operation_rule.return_value = rule
        baas.list_devices_by_bot_uuid.return_value = [
            {"device_uuid": "D1", "provider_device_id": "P1"},
            {"device_uuid": "D2"},
            {"device_uuid": "D3", "provider_device_id": "P3"},
        ]
        svc = _make_service(baas_service=baas)
        device = AllocatedDevice(
            device_id="BOT-fallback",
            device_provider=BAAS_DEVICE_PROVIDER,
            device_props={"bolt_id": "bot-1", "entity_id": "u1", "bot_uuid": "BOT-1"},
        )

        result = svc.update_device_headers(device=device)

        baas.list_devices_by_bot_uuid.assert_called_once_with("BOT-1")
        assert result == [
            {"device_uuid": "D1", "paas_device_id": "P1"},
            {"device_uuid": "D3", "paas_device_id": "P3"},
        ]
        baas.update_device_outbound_rule.assert_any_call("P1", rule)
        baas.update_device_outbound_rule.assert_any_call("P3", rule)

    def test_batch_mode_returns_empty_when_no_devices_found(self):
        baas = MagicMock()
        baas._build_outbound_operation_rule.return_value = MagicMock()
        baas.list_devices_by_bot_uuid.return_value = []
        svc = _make_service(baas_service=baas)
        device = AllocatedDevice(
            device_id="BOT-fallback",
            device_provider=BAAS_DEVICE_PROVIDER,
            device_props={"bolt_id": "bot-1", "entity_id": "u1"},
        )

        assert svc.update_device_headers(device=device) == []
        baas.list_devices_by_bot_uuid.assert_called_once_with("BOT-fallback")
        baas.update_device_outbound_rule.assert_not_called()


class TestGetDeviceConnection:
    def _ws_info(self):
        info = MagicMock()
        info.target = "BAAS_DEVICE@template:20003"
        info.token = "token-1"
        info.baas_base_url = "http://baas.local"
        info.bot_uuid = "BOT-1"
        info.tenant = "team_claw"
        info.engine_port = 20003
        return info

    def test_returns_baas_connection_with_bot_engine(self):
        baas = MagicMock()
        baas.get_ws_info.return_value = self._ws_info()
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = {
            "bot_id": "bot-1",
            "bot_type": "personal",
            "active_engine": "claude_code",
        }
        svc = _make_service(baas_service=baas, bot_query=bot_query)

        result = svc.get_device_connection(binding_id=7, operator=_operator("u001"))

        baas.get_ws_info.assert_called_once_with(
            bind_id=7,
            device_affinity="u001",
            device_uuid=None,
            ws_conn_mode=None,
        )
        assert result.type == "baas"
        assert result.target == "BAAS_DEVICE@template:20003"
        assert result.token == "token-1"
        assert result.engine_type == "claude_code"
        assert result.bot_uuid == "BOT-1"

    def test_desktop_bot_returns_desktop_connection_type(self):
        baas = MagicMock()
        baas.get_ws_info.return_value = self._ws_info()
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = {
            "bot_id": "desktop-1",
            "bot_type": "desktop",
            "active_engine": "openclaw",
        }
        svc = _make_service(baas_service=baas, bot_query=bot_query)

        result = svc.get_device_connection(binding_id=8, operator=_operator("u001"))

        assert result.type == "desktop"
        assert result.engine_type == "openclaw"

    def test_ws_info_failure_is_wrapped(self):
        from agentclaw.community.core.service_bot.services.baas_service import BaasServiceError

        baas = MagicMock()
        baas.get_ws_info.side_effect = BaasServiceError("ws down")
        svc = _make_service(baas_service=baas)

        with pytest.raises(BaasDeviceServiceError, match="device connection"):
            svc.get_device_connection(binding_id=9, operator=_operator("u001"))


class TestExecShellNew:
    def test_exec_shell_new_delegates_to_baas_exec_command(self):
        baas = MagicMock()
        baas.exec_command_on_bot.return_value = {
            "stdout": '{"servers":[]}',
            "stderr": "",
            "exit_code": 0,
            "execution_time_ms": 42,
        }
        svc = _make_service(baas_service=baas)
        device = AllocatedDevice(
            device_id="BOT-fallback",
            device_provider=BAAS_DEVICE_PROVIDER,
            device_props={"bot_uuid": "BOT-real"},
        )

        result = svc._exec_shell_new(
            device=device,
            shell_cmd="cat /home/admin/.mcporter/mcporter.json",
        )

        baas.exec_command_on_bot.assert_called_once_with(
            bot_uuid="BOT-real",
            cmd="cat /home/admin/.mcporter/mcporter.json",
            timeout_seconds=30,
        )
        assert result.stdout == '{"servers":[]}'
        assert result.stderr == ""
        assert result.exit_code == 0
        assert result.elapsed_time == 0.042
        assert result.status == "completed"
        assert result.error is None

    def test_exec_shell_new_falls_back_to_device_id_when_bot_uuid_missing(self):
        baas = MagicMock()
        baas.exec_command_on_bot.return_value = {
            "stdout": "ok",
            "stderr": "",
            "exit_code": 0,
        }
        svc = _make_service(baas_service=baas)
        device = AllocatedDevice(
            device_id="BOT-fallback",
            device_provider=BAAS_DEVICE_PROVIDER,
            device_props={},
        )

        svc._exec_shell_new(device=device, shell_cmd="pwd")

        baas.exec_command_on_bot.assert_called_once_with(
            bot_uuid="BOT-fallback",
            cmd="pwd",
            timeout_seconds=30,
        )

    def test_exec_shell_new_wraps_baas_exec_failure(self):
        baas = MagicMock()
        baas.exec_command_on_bot.side_effect = RuntimeError("baas timeout")
        svc = _make_service(baas_service=baas)
        device = AllocatedDevice(
            device_id="BOT-fallback",
            device_provider=BAAS_DEVICE_PROVIDER,
            device_props={"bot_uuid": "BOT-real"},
        )

        with pytest.raises(DeviceExecShellError, match="BaaS exec_shell fail"):
            svc._exec_shell_new(device=device, shell_cmd="pwd")

        baas.exec_command_on_bot.assert_called_once_with(
            bot_uuid="BOT-real",
            cmd="pwd",
            timeout_seconds=30,
        )


# ===========================================================================
# Part 1: _resolve_bot_by_binding_id
# ===========================================================================


class TestResolveBotByBindingId:
    def test_found_by_binding_id_directly(self):
        """桌面 BOT：ac_bots.binding_id 直接命中，不走 fallback。"""
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = {"bot_id": "b1", "bot_type": "desktop"}
        svc = _make_service(bot_query=bot_query)

        result = svc._resolve_bot_by_binding_id(1)

        assert result == {"bot_id": "b1", "bot_type": "desktop"}
        bot_query.get_by_id_and_owner.assert_not_called()

    def test_fallback_via_binding_record(self):
        """服务 BOT：直接查不到，通过 binding 的 bolt_id + entity_id 查到。"""
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = None
        bot_query.get_by_id_and_owner.return_value = {"bot_id": "b2", "bot_type": "service"}

        repo = MagicMock()
        repo.get_by_id.return_value = _make_binding_record(
            entity_id="u001",
            device_props={"bolt_id": "b2"},
        )
        svc = _make_service(repo=repo, bot_query=bot_query)

        result = svc._resolve_bot_by_binding_id(1)

        assert result == {"bot_id": "b2", "bot_type": "service"}
        bot_query.get_by_id_and_owner.assert_called_once_with("b2", "u001")

    def test_binding_not_found_returns_none(self):
        """binding_id 在两张表都查不到。"""
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = None

        repo = MagicMock()
        repo.get_by_id.return_value = None
        svc = _make_service(repo=repo, bot_query=bot_query)

        result = svc._resolve_bot_by_binding_id(99)

        assert result is None
        bot_query.get_by_id_and_owner.assert_not_called()

    def test_binding_without_bolt_id_returns_none(self):
        """binding 记录存在但 device_props 里没有 bolt_id。"""
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = None

        repo = MagicMock()
        repo.get_by_id.return_value = _make_binding_record(device_props={})
        svc = _make_service(repo=repo, bot_query=bot_query)

        result = svc._resolve_bot_by_binding_id(1)

        assert result is None
        bot_query.get_by_id_and_owner.assert_not_called()

    def test_binding_with_none_device_props_returns_none(self):
        """binding 记录的 device_props 为 None。"""
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = None

        repo = MagicMock()
        repo.get_by_id.return_value = _make_binding_record(device_props=None)
        svc = _make_service(repo=repo, bot_query=bot_query)

        result = svc._resolve_bot_by_binding_id(1)

        assert result is None

    def test_fallback_bot_not_found_returns_none(self):
        """binding 有 bolt_id 但 ac_bots 按 bot_id+owner_id 也查不到。"""
        bot_query = MagicMock()
        bot_query.get_by_binding_id.return_value = None
        bot_query.get_by_id_and_owner.return_value = None

        repo = MagicMock()
        repo.get_by_id.return_value = _make_binding_record(
            entity_id="u001",
            device_props={"bolt_id": "nonexistent"},
        )
        svc = _make_service(repo=repo, bot_query=bot_query)

        result = svc._resolve_bot_by_binding_id(1)

        assert result is None
        bot_query.get_by_id_and_owner.assert_called_once_with("nonexistent", "u001")


# ===========================================================================
# Part 2: provider=baas lifecycle hooks
# ===========================================================================


# ---------------------------------------------------------------------------
# _setup_directory
# ---------------------------------------------------------------------------


class TestSetupDirectory:
    def test_returns_empty_list(self):
        """provider=baas doesn't need OCB-side directory prep — the
        template image owns its own mount points."""
        svc = _make_service()
        result = svc._setup_directory(
            _operator(),
            entity_id="staff_u001",
            entity_type="staff",
            bolt_id="default",
            env="pre",
            engine="claude_code",
        )
        assert result == []


# ---------------------------------------------------------------------------
# _do_allocate / _do_allocate_nas
# ---------------------------------------------------------------------------


class TestDoAllocate:
    def _setup_baas(self) -> MagicMock:
        baas = MagicMock()
        baas._baas_api_base = "http://baas.local"
        baas._build_personal_bot_payload.return_value = {"fake": "payload"}
        baas._build_create_bot_payload.return_value = {"fake": "service_payload"}
        baas.post_bots_api.return_value = {
            "bot_uuid": "BAAS-CTR-xxx",
            "publish_id": 12345,
        }
        baas.approve_publish.return_value = {}
        return baas

    def test_happy_path_returns_allocated_device_with_baas_props(self):
        baas = self._setup_baas()
        svc = _make_service(baas_service=baas)

        allocated = svc._do_allocate(
            entity_id="staff_u001",
            entity_type="staff",
            bolt_id="default",
            device_id="staff_staff_u001_default_abc",
            storage_mappings=[],
            env="pre",
            engine="claude_code",
            bot_type="personal",
            extra_envs={"BOT_TYPE": "personalCoding"},
            template_type="personalCoding",
            template_config={"template_uid": "aicoding_personal_default"},
        )

        # device_id 落 BaaS 真实 bot_uuid(与 service bot 口径统一);
        # 本地拼装 id 留 props.local_device_id 备查。回归守护:trace
        # 0b446a4d17822154217278160e3e69(staff_ 查 BaaS 404 BOT_NOT_FOUND)。
        assert allocated.device_id == "BAAS-CTR-xxx"
        assert allocated.device_props["local_device_id"] == "staff_staff_u001_default_abc"
        assert allocated.device_provider == BAAS_DEVICE_PROVIDER
        assert allocated.device_props["bot_uuid"] == "BAAS-CTR-xxx"
        assert allocated.device_props["publish_id"] == "12345"
        assert allocated.device_props["device_from"] == "baas"
        assert allocated.device_props["envs"] == {"BOT_TYPE": "personalCoding"}
        baas._build_personal_bot_payload.assert_not_called()
        baas._build_create_bot_payload.assert_called_once()
        builder_kwargs = baas._build_create_bot_payload.call_args.kwargs
        assert builder_kwargs["bot"]["bot_type"] == "personal"
        assert builder_kwargs["bot"]["active_engine"] == "claude_code"
        assert builder_kwargs["migration_path"] == ""
        assert builder_kwargs["mount_home_dir_storage"] is True
        assert "stage" not in builder_kwargs
        assert builder_kwargs["auto_approve_publish"] is True
        assert builder_kwargs["extra_envs"] == {"BOT_TYPE": "personalCoding"}
        assert builder_kwargs["template_config"] == {"template_uid": "aicoding_personal_default"}
        baas.post_bots_api.assert_called_once()
        baas.approve_publish.assert_not_called()

    def test_personal_create_requires_upstream_template_uid_and_resolves_uuid(self):
        baas = self._setup_baas()
        template_resolver = MagicMock()
        template_resolver.resolve_template.return_value = BaasTemplateResolution(
            template_uid="openclaw_personal_default",
            template_uuid="TEMPLATE-openclaw-personal",
            source="template_config",
        )
        svc = _make_service(
            baas_service=baas,
            template_resolver=template_resolver,
        )

        allocated = svc._do_allocate(
            entity_id="staff_u001",
            entity_type="staff",
            bolt_id="default",
            device_id="staff_u001_abc",
            storage_mappings=[],
            env="prod",
            engine="openclaw",
            bot_type="personal",
            template_type="normalCC",
            template_config={"template_uid": "openclaw_personal_default"},
        )
        template_resolver.resolve_template_uid.assert_not_called()
        template_resolver.resolve_template.assert_called_once_with(
            bot_id="default",
            user_id="staff_u001",
            env="prod",
            bot_type="personal",
            engine_type="openclaw",
            template_type="normalCC",
            template_config={"template_uid": "openclaw_personal_default"},
        )
        kwargs = baas._build_create_bot_payload.call_args.kwargs
        assert kwargs["template_uuid"] == "TEMPLATE-openclaw-personal"
        assert allocated.device_props["template_uid"] == "openclaw_personal_default"
        assert allocated.device_props["template_uuid"] == "TEMPLATE-openclaw-personal"

    def test_explicit_template_uid_wins_and_request_uuid_is_ignored(self):
        baas = self._setup_baas()
        template_resolver = MagicMock()
        template_resolver.resolve_template.return_value = BaasTemplateResolution(
            template_uid="custom_business_uid",
            template_uuid="TEMPLATE-system-custom",
            source="template_config",
        )
        template_config = {
            "template_uid": "custom_business_uid",
            "template_uuid": "TEMPLATE-request-should-not-win",
            "image_id": "IMAGE-1",
            "tenant_id": "TENANT-1",
        }
        svc = _make_service(
            baas_service=baas,
            template_resolver=template_resolver,
        )

        svc._do_allocate(
            entity_id="staff_u001",
            entity_type="staff",
            bolt_id="default",
            device_id="staff_u001_abc",
            storage_mappings=[],
            env="prod",
            engine="openclaw",
            bot_type="personal",
            template_config=template_config,
        )

        template_resolver.resolve_template_uid.assert_not_called()
        template_resolver.resolve_template.assert_called_once()
        kwargs = baas._build_create_bot_payload.call_args.kwargs
        assert kwargs["template_uuid"] == "TEMPLATE-system-custom"
        assert kwargs["template_config"] == template_config

    def test_missing_template_uid_stops_before_baas_create(self):
        baas = self._setup_baas()
        template_resolver = MagicMock()
        svc = _make_service(
            baas_service=baas,
            template_resolver=template_resolver,
        )

        with pytest.raises(BaasDeviceServiceError, match="template_uid"):
            svc._do_allocate(
                entity_id="staff_u001",
                entity_type="staff",
                bolt_id="default",
                device_id="staff_u001_abc",
                storage_mappings=[],
                env="prod",
                engine="openclaw",
                bot_type="personal",
                template_config=None,
            )

        template_resolver.resolve_template_uuid.assert_not_called()
        template_resolver.resolve_template.assert_not_called()
        baas._build_personal_bot_payload.assert_not_called()
        baas.post_bots_api.assert_not_called()

    def test_upstream_template_uid_resolution_error_is_reported(self):
        baas = self._setup_baas()
        template_resolver = MagicMock()
        svc = _make_service(
            baas_service=baas,
            template_resolver=template_resolver,
        )

        with pytest.raises(BaasDeviceServiceError, match="selector missing"):
            svc._do_allocate(
                entity_id="staff_u001",
                entity_type="staff",
                bolt_id="default",
                device_id="staff_u001_abc",
                storage_mappings=[],
                env="prod",
                engine="openclaw",
                bot_type="personal",
                template_config={
                    "template_uid": None,
                    "_baas_template_uid_resolution_error": "selector missing",
                },
            )

        template_resolver.resolve_template_uuid.assert_not_called()
        template_resolver.resolve_template.assert_not_called()
        baas._build_personal_bot_payload.assert_not_called()
        baas.post_bots_api.assert_not_called()

    def test_template_uuid_resolution_failure_stops_before_baas_create(self):
        from agentclaw.community.core.devices.services.baas_template_resolver import (
            BaasTemplateResolveError,
        )

        baas = self._setup_baas()
        template_resolver = MagicMock()
        template_resolver.resolve_template.side_effect = BaasTemplateResolveError(
            "selector missing"
        )
        svc = _make_service(
            baas_service=baas,
            template_resolver=template_resolver,
        )

        with pytest.raises(BaasDeviceServiceError, match="selector missing"):
            svc._do_allocate(
                entity_id="staff_u001",
                entity_type="staff",
                bolt_id="default",
                device_id="staff_u001_abc",
                storage_mappings=[],
                env="prod",
                engine="openclaw",
                bot_type="personal",
                template_config={"template_uid": "openclaw_personal_default"},
            )

        baas._build_personal_bot_payload.assert_not_called()
        baas.post_bots_api.assert_not_called()

    def test_personal_create_uses_generic_payload_builder_with_extra_envs(self):
        baas = self._setup_baas()
        svc = _make_service(baas_service=baas)

        svc._do_allocate(
            entity_id="staff_u001",
            entity_type="staff",
            bolt_id="default",
            device_id="staff_u001_abc",
            storage_mappings=[],
            env="pre",
            bot_type="personal",
            extra_envs={"AIX_DEVFLOW_INFO": "foo"},
            template_config={"template_uid": "aicoding_personal_default"},
        )
        baas._build_personal_bot_payload.assert_not_called()
        kwargs = baas._build_create_bot_payload.call_args.kwargs
        assert kwargs["extra_envs"] == {"AIX_DEVFLOW_INFO": "foo"}
        assert kwargs["bot"]["bot_id"] == "default"
        assert kwargs["bot"]["entity_id"] == "staff_u001"
        assert kwargs["bot"]["bot_type"] == "personal"
        assert kwargs["owner_id"] == "staff_u001"

    def test_service_draft_uses_generic_payload_builder_not_personal_payload(self):
        baas = self._setup_baas()
        baas.post_bots_api.return_value = {
            "bot_uuid": "BAAS-SERVICE-DRAFT-1",
            "publish_id": 22345,
        }
        svc = _make_service(baas_service=baas)

        allocated = svc._do_allocate(
            entity_id="staff_u001",
            entity_type="staff",
            bolt_id="service-bot-1",
            device_id="staff_u001_service_abc",
            storage_mappings=[],
            env="pre",
            engine="openclaw",
            bot_type="service",
            template_config={"template_uid": "openclaw_service_draft_default"},
        )

        assert allocated.device_provider == BAAS_DEVICE_PROVIDER
        assert allocated.device_props["bot_uuid"] == "BAAS-SERVICE-DRAFT-1"
        baas._build_personal_bot_payload.assert_not_called()
        baas._build_create_bot_payload.assert_called_once()
        builder_kwargs = baas._build_create_bot_payload.call_args.kwargs
        assert builder_kwargs["bot"]["bot_id"] == "service-bot-1"
        assert builder_kwargs["bot"]["bot_type"] == "service"
        assert builder_kwargs["bot"]["active_engine"] == "openclaw"
        assert builder_kwargs["migration_path"] == ""
        assert builder_kwargs["mount_home_dir_storage"] is True
        assert builder_kwargs["stage"] == "draft"
        assert builder_kwargs["auto_approve_publish"] is True
        baas.create_bot.assert_not_called()
        baas.post_bots_api.assert_called_once_with(
            path="/api/v1/bots",
            payload={"fake": "service_payload"},
            action="baas_device_create",
        )
        baas.approve_publish.assert_not_called()

    def test_service_draft_uses_resolved_template_uuid(self):
        baas = self._setup_baas()
        template_resolver = MagicMock()
        template_resolver.resolve_template.return_value = BaasTemplateResolution(
            template_uid="openclaw_service_draft_default",
            template_uuid="TEMPLATE-service-draft",
            source="template_config",
        )
        svc = _make_service(
            baas_service=baas,
            template_resolver=template_resolver,
        )

        svc._do_allocate(
            entity_id="staff_u001",
            entity_type="staff",
            bolt_id="service-bot-1",
            device_id="staff_u001_service_abc",
            storage_mappings=[],
            env="pre",
            engine="openclaw",
            bot_type="service",
            template_type="draft",
            template_config={"template_uid": "openclaw_service_draft_default"},
        )

        template_resolver.resolve_template_uid.assert_not_called()
        template_resolver.resolve_template.assert_called_once()
        builder_kwargs = baas._build_create_bot_payload.call_args.kwargs
        assert builder_kwargs["template_uuid"] == "TEMPLATE-service-draft"

    def test_missing_bot_type_is_rejected(self):
        baas = self._setup_baas()
        svc = _make_service(baas_service=baas)

        with pytest.raises(BaasDeviceServiceError, match="bot_type"):
            svc._do_allocate(
                entity_id="staff_u001",
                entity_type="staff",
                bolt_id="default",
                device_id="staff_u001_abc",
                storage_mappings=[],
                env="pre",
            )

        baas._build_personal_bot_payload.assert_not_called()
        baas.create_bot.assert_not_called()
        baas.post_bots_api.assert_not_called()

    def test_unknown_bot_type_is_rejected(self):
        baas = self._setup_baas()
        svc = _make_service(baas_service=baas)

        with pytest.raises(BaasDeviceServiceError, match="unsupported bot_type"):
            svc._do_allocate(
                entity_id="staff_u001",
                entity_type="staff",
                bolt_id="desktop-bot",
                device_id="staff_u001_desktop_abc",
                storage_mappings=[],
                env="pre",
                bot_type="desktop",
            )

        baas._build_personal_bot_payload.assert_not_called()
        baas.create_bot.assert_not_called()
        baas.post_bots_api.assert_not_called()

    def test_post_bots_failure_raises_baas_device_error(self):
        from agentclaw.community.core.service_bot.services.baas_service import BaasServiceError

        baas = self._setup_baas()
        baas.post_bots_api.side_effect = BaasServiceError("boom")
        svc = _make_service(baas_service=baas)

        with pytest.raises(BaasDeviceServiceError):
            svc._do_allocate(
                entity_id="staff_u001",
                entity_type="staff",
                bolt_id="default",
                device_id="staff_u001_abc",
                storage_mappings=[],
                env="pre",
                bot_type="personal",
                template_config={"template_uid": "openclaw_personal_default"},
            )
        baas.approve_publish.assert_not_called()

    def test_payload_builder_failure_raises_baas_device_error(self):
        from agentclaw.community.core.service_bot.services.baas_service import BaasServiceError

        baas = self._setup_baas()
        baas._build_create_bot_payload.side_effect = BaasServiceError("missing template")
        svc = _make_service(baas_service=baas)

        with pytest.raises(BaasDeviceServiceError, match="missing template"):
            svc._do_allocate(
                entity_id="staff_u001",
                entity_type="staff",
                bolt_id="default",
                device_id="staff_u001_abc",
                storage_mappings=[],
                env="pre",
                bot_type="personal",
                template_config={"template_uid": "openclaw_personal_default"},
            )
        baas.post_bots_api.assert_not_called()
        baas.approve_publish.assert_not_called()

    def test_create_skips_manual_approve_when_payload_requests_auto_approve(self):
        from agentclaw.community.core.service_bot.services.baas_service import BaasServiceError

        baas = self._setup_baas()
        baas.approve_publish.side_effect = BaasServiceError("approve failed")
        svc = _make_service(baas_service=baas)

        allocated = svc._do_allocate(
            entity_id="staff_u001",
            entity_type="staff",
            bolt_id="default",
            device_id="staff_u001_abc",
            storage_mappings=[],
            env="pre",
            bot_type="personal",
            template_config={"template_uid": "openclaw_personal_default"},
        )

        assert allocated.device_provider == BAAS_DEVICE_PROVIDER
        baas.approve_publish.assert_not_called()

    def test_missing_bot_uuid_raises(self):
        baas = self._setup_baas()
        baas.post_bots_api.return_value = {"publish_id": 12345}
        svc = _make_service(baas_service=baas)

        with pytest.raises(BaasDeviceServiceError, match="bot_uuid"):
            svc._do_allocate(
                entity_id="staff_u001",
                entity_type="staff",
                bolt_id="default",
                device_id="staff_u001_abc",
                storage_mappings=[],
                env="pre",
                bot_type="personal",
                template_config={"template_uid": "openclaw_personal_default"},
            )

    def test_do_allocate_nas_delegates_to_baas_path(self):
        """provider=baas doesn't distinguish OSS vs NAS — both end up at
        ``POST /api/v1/bots``."""
        baas = self._setup_baas()
        svc = _make_service(baas_service=baas)

        allocated = svc._do_allocate_nas(
            entity_id="staff_u001",
            entity_type="staff",
            bolt_id="default",
            device_id="staff_u001_nas",
            env="pre",
            create_bot_type="personal",
            extra_envs={"BOT_TYPE": "personalCoding"},
            template_config={"template_uid": "aicoding_personal_default"},
        )
        assert allocated.device_provider == BAAS_DEVICE_PROVIDER
        baas.post_bots_api.assert_called_once()


# ---------------------------------------------------------------------------
# _start_service (polling)
# ---------------------------------------------------------------------------


def _device_with_publish(publish_id: str = "12345") -> AllocatedDevice:
    return AllocatedDevice(
        device_id="staff_u001_default_abc",
        device_provider=BAAS_DEVICE_PROVIDER,
        device_props={
            "bot_uuid": "BAAS-CTR-xxx",
            "publish_id": publish_id,
            "callback_token": "tok-123",
            "device_from": "baas",
        },
    )


class TestStartService:
    def test_missing_publish_id_returns_failure(self):
        svc = _make_service()
        device = AllocatedDevice(
            device_id="x", device_provider=BAAS_DEVICE_PROVIDER,
            device_props={"callback_token": "t"},
        )
        ok, msg = svc._start_service(device=device)
        assert ok is False
        assert "publish_id" in msg

    def test_invalid_publish_id_returns_failure(self):
        svc = _make_service()
        device = AllocatedDevice(
            device_id="x", device_provider=BAAS_DEVICE_PROVIDER,
            device_props={"publish_id": "not-a-number", "callback_token": "t"},
        )
        ok, msg = svc._start_service(device=device)
        assert ok is False
        assert "invalid publish_id" in msg

    def test_success_calls_report_device_alive(self):
        baas = MagicMock()
        baas._baas_api_base = "http://baas.local"
        baas.get_publish_progress.return_value = {"status": "SUCCESS"}
        baas.exec_command_on_bot.return_value = {"exit_code": 0, "stdout": "", "stderr": ""}
        svc = _make_service(baas_service=baas)
        svc.report_device_alive = MagicMock()

        with patch(
            "agentclaw.community.core.devices.services.baas_device_service.time.sleep",
            return_value=None,
        ):
            ok, msg = svc._start_service(device=_device_with_publish())

        assert ok is True
        svc.report_device_alive.assert_called_once_with(
            device_id="staff_u001_default_abc",
            token="tok-123",
        )

    def test_failed_returns_failure_for_parent_to_mark_failed(self):
        baas = MagicMock()
        baas._baas_api_base = "http://baas.local"
        baas.get_publish_progress.return_value = {"status": "FAILED"}
        svc = _make_service(baas_service=baas)
        svc.report_device_alive = MagicMock()

        with patch(
            "agentclaw.community.core.devices.services.baas_device_service.time.sleep",
            return_value=None,
        ):
            ok, msg = svc._start_service(device=_device_with_publish())

        assert ok is False
        assert "FAILED" in msg
        svc.report_device_alive.assert_not_called()

    def test_transient_error_is_retried(self):
        from agentclaw.community.core.service_bot.services.baas_service import BaasServiceError

        baas = MagicMock()
        baas._baas_api_base = "http://baas.local"
        baas.get_publish_progress.side_effect = [
            BaasServiceError("flake"),
            {"status": "SUCCESS"},
        ]
        baas.exec_command_on_bot.return_value = {"exit_code": 0}
        svc = _make_service(baas_service=baas)
        svc.report_device_alive = MagicMock()

        with patch(
            "agentclaw.community.core.devices.services.baas_device_service.time.sleep",
            return_value=None,
        ):
            ok, _ = svc._start_service(device=_device_with_publish())

        assert ok is True
        assert baas.get_publish_progress.call_count == 2

    def test_timeout_returns_failure(self):
        baas = MagicMock()
        baas._baas_api_base = "http://baas.local"
        baas.get_publish_progress.return_value = {"status": "PENDING"}
        svc = _make_service(baas_service=baas)

        times = iter([0.0, 999_999.0])

        def _fake_monotonic():
            try:
                return next(times)
            except StopIteration:
                return 999_999.0

        with patch(
            "agentclaw.community.core.devices.services.baas_device_service.time.sleep",
            return_value=None,
        ), patch(
            "agentclaw.community.core.devices.services.baas_device_service.time.monotonic",
            side_effect=_fake_monotonic,
        ):
            ok, msg = svc._start_service(device=_device_with_publish())

        assert ok is False
        assert "timeout" in msg.lower()


class TestStartServiceInitSteps:
    """Tests for the 6-step container init that runs after publish SUCCESS."""

    def _make_success_svc(self):
        baas = MagicMock()
        baas._baas_api_base = "http://baas.local"
        baas.get_publish_progress.return_value = {"status": "SUCCESS"}
        baas.exec_command_on_bot.return_value = {"exit_code": 0, "stdout": "", "stderr": ""}
        svc = _make_service(baas_service=baas)
        svc.report_device_alive = MagicMock()
        return svc, baas

    def test_init_commands_called_in_order(self):
        svc, baas = self._make_success_svc()

        with patch(
            "agentclaw.community.core.devices.services.baas_device_service.time.sleep",
            return_value=None,
        ):
            ok, _ = svc._start_service(device=_device_with_publish(), engine="aicoding")

        assert ok is True
        calls = baas.exec_command_on_bot.call_args_list
        cmds = [c.kwargs.get("cmd", c[1].get("cmd", "")) if c.kwargs else "" for c in calls]
        if not cmds[0]:
            cmds = [c[1]["cmd"] for c in calls]

        assert "bootstrap_minimal.sh" in cmds[0]
        assert "install_engine.sh" in cmds[1]
        assert "setup_supervisor_sync_service.sh" in cmds[2]
        assert "setup_engine_dirs.sh" in cmds[3]
        assert "start_service.sh" in cmds[-2]
        assert "starting_watchdog.sh" in cmds[-1]

    def test_no_su_admin_in_commands(self):
        svc, baas = self._make_success_svc()

        with patch(
            "agentclaw.community.core.devices.services.baas_device_service.time.sleep",
            return_value=None,
        ):
            svc._start_service(device=_device_with_publish())

        for call in baas.exec_command_on_bot.call_args_list:
            cmd = call.kwargs.get("cmd", "")
            assert "su admin" not in cmd, f"BaaS container should not use su admin: {cmd}"

    def test_init_uses_correct_bot_uuid(self):
        svc, baas = self._make_success_svc()

        with patch(
            "agentclaw.community.core.devices.services.baas_device_service.time.sleep",
            return_value=None,
        ):
            svc._start_service(device=_device_with_publish())

        for call in baas.exec_command_on_bot.call_args_list:
            assert call.kwargs.get("bot_uuid") == "BAAS-CTR-xxx"

    def test_init_failure_returns_false(self):
        from agentclaw.community.core.service_bot.services.baas_service import BaasServiceError

        baas = MagicMock()
        baas._baas_api_base = "http://baas.local"
        baas.get_publish_progress.return_value = {"status": "SUCCESS"}
        baas.exec_command_on_bot.side_effect = BaasServiceError("bootstrap failed")
        svc = _make_service(baas_service=baas)
        svc.report_device_alive = MagicMock()

        with patch(
            "agentclaw.community.core.devices.services.baas_device_service.time.sleep",
            return_value=None,
        ):
            ok, msg = svc._start_service(device=_device_with_publish())

        assert ok is False
        assert "init failed" in msg.lower() or "bootstrap" in msg.lower()
        svc.report_device_alive.assert_not_called()

    def test_start_service_passes_bot_type(self):
        svc, baas = self._make_success_svc()

        with patch(
            "agentclaw.community.core.devices.services.baas_device_service.time.sleep",
            return_value=None,
        ):
            svc._start_service(
                device=_device_with_publish(),
                bot_type="personalCoding",
                engine="aicoding",
            )

        start_cmd_calls = [
            c for c in baas.exec_command_on_bot.call_args_list
            if "start_service.sh" in c.kwargs.get("cmd", "")
        ]
        assert len(start_cmd_calls) == 1
        assert "--bot_type personalCoding" in start_cmd_calls[0].kwargs["cmd"]


# ---------------------------------------------------------------------------
# _do_release / _query_device_info
# ---------------------------------------------------------------------------


class TestDoRelease:
    def test_destroy_bot_called_with_bot_uuid_from_props(self):
        baas = MagicMock()
        svc = _make_service(baas_service=baas)
        device = AllocatedDevice(
            device_id="staff_u001_default_abc",
            device_provider=BAAS_DEVICE_PROVIDER,
            device_props={
                "bot_uuid": "BAAS-CTR-xxx",
                "entity_id": "u001",
                "entity_type": "staff",
                "bolt_id": "default",
                "env": "pre",
            },
        )

        ok = svc._do_release(device=device)
        assert ok is True
        baas.destroy_bot.assert_called_once()
        kwargs = baas.destroy_bot.call_args.kwargs
        assert kwargs["bot_uuid"] == "BAAS-CTR-xxx"
        assert kwargs["operator"] == "u001"

    def test_destroy_publish_is_auto_approved_when_returned(self):
        baas = MagicMock()
        baas.destroy_bot.return_value = {"publish_id": 67890}
        svc = _make_service(baas_service=baas)
        device = AllocatedDevice(
            device_id="staff_u001_default_abc",
            device_provider=BAAS_DEVICE_PROVIDER,
            device_props={
                "bot_uuid": "BAAS-CTR-xxx",
                "entity_id": "u001",
                "entity_type": "staff",
                "bolt_id": "default",
                "env": "pre",
            },
        )

        ok = svc._do_release(device=device)

        assert ok is True
        baas.approve_publish.assert_called_once()
        kwargs = baas.approve_publish.call_args.kwargs
        assert kwargs["publish_id"] == 67890
        assert kwargs["operator"] == "u001"
        assert kwargs["comment"] == "自动审批销毁"

    def test_skips_when_no_bot_uuid(self):
        baas = MagicMock()
        svc = _make_service(baas_service=baas)
        device = AllocatedDevice(
            device_id="",
            device_provider=BAAS_DEVICE_PROVIDER,
            device_props={"entity_id": "u001"},
        )

        ok = svc._do_release(device=device)
        assert ok is True
        baas.destroy_bot.assert_not_called()

    def test_destroy_bot_failure_raises(self):
        from agentclaw.community.core.service_bot.services.baas_service import BaasServiceError

        baas = MagicMock()
        baas.destroy_bot.side_effect = BaasServiceError("nope")
        svc = _make_service(baas_service=baas)
        device = AllocatedDevice(
            device_id="staff_u001_default_abc",
            device_provider=BAAS_DEVICE_PROVIDER,
            device_props={
                "bot_uuid": "BAAS-CTR-xxx",
                "entity_id": "u001",
            },
        )
        with pytest.raises(BaasDeviceServiceError):
            svc._do_release(device=device)


class TestQueryDeviceInfo:
    def test_returns_bot_and_publish_metadata(self):
        baas = MagicMock()
        baas.list_devices_by_bot_uuid.return_value = [
            {"device_uuid": "DEV-1", "provider_device_id": "PAAS-1"},
        ]
        svc = _make_service(baas_service=baas)
        device = AllocatedDevice(
            device_id="staff_u001_default_abc",
            device_provider=BAAS_DEVICE_PROVIDER,
            device_props={"bot_uuid": "BAAS-CTR-xxx", "publish_id": "12345"},
        )

        info = svc._query_device_info(device=device)
        assert info["bot_uuid"] == "BAAS-CTR-xxx"
        assert info["publish_id"] == "12345"
        assert info["device_count"] == 1
        assert info["device"]["device_uuid"] == "DEV-1"

    def test_list_devices_failure_returns_empty(self):
        from agentclaw.community.core.service_bot.services.baas_service import BaasServiceError

        baas = MagicMock()
        baas.list_devices_by_bot_uuid.side_effect = BaasServiceError("down")
        svc = _make_service(baas_service=baas)
        device = AllocatedDevice(
            device_id="x",
            device_provider=BAAS_DEVICE_PROVIDER,
            device_props={"bot_uuid": "BAAS-CTR-xxx", "publish_id": "12345"},
        )
        info = svc._query_device_info(device=device)
        assert info["device_count"] == 0
        assert info["device"] == {}


# ===========================================================================
# Additional coverage: error paths and edge cases
# ===========================================================================


class TestStartServiceErrorPaths:
    """Cover error branches in _start_service."""

    def test_publish_success_missing_bot_uuid(self):
        """Line 353: publish SUCCESS but device_props has no bot_uuid."""
        baas = MagicMock()
        baas._baas_api_base = "http://baas.local"
        baas.get_publish_progress.return_value = {"status": "SUCCESS"}
        svc = _make_service(baas_service=baas)

        device = _device_with_publish()
        device.device_props = {"publish_id": "12345", "callback_token": "tok"}  # No bot_uuid

        with patch("agentclaw.community.core.devices.services.baas_device_service.time.sleep"):
            ok, msg = svc._start_service(device=device, engine="aicoding")

        assert ok is False
        assert "missing bot_uuid" in msg

    def test_report_device_alive_failure(self):
        """Lines 378-382: report_device_alive fails after SUCCESS."""
        baas = MagicMock()
        baas._baas_api_base = "http://baas.local"
        baas.get_publish_progress.return_value = {"status": "SUCCESS"}
        baas.exec_command_on_bot.return_value = {"exit_code": 0}
        svc = _make_service(baas_service=baas)
        svc.report_device_alive = MagicMock(side_effect=RuntimeError("network timeout"))

        with patch("agentclaw.community.core.devices.services.baas_device_service.time.sleep"):
            ok, msg = svc._start_service(device=_device_with_publish(), engine="aicoding")

        assert ok is False
        assert "report_device_alive failed" in msg


class TestInitStepEdgeCases:
    """Cover container init helper edge cases."""

    def test_ensure_baas_engine_dirs_failure_non_fatal(self):
        """Lines 472-473: exec_command_on_bot raises but is caught."""
        baas = MagicMock()
        baas._baas_api_base = "http://baas.local"
        baas.exec_command_on_bot.side_effect = RuntimeError("timeout")
        svc = _make_service(baas_service=baas)

        # Should not raise
        svc._ensure_baas_engine_dirs(bot_uuid="BOT-1", engine="aicoding")

    def test_create_baas_skill_symlink_conf_writes_config(self):
        """Lines 480-490: skill symlink conf is written."""
        import json
        baas = MagicMock()
        baas._baas_api_base = "http://baas.local"
        svc = _make_service(baas_service=baas)

        symbol = json.dumps([{"source": "./skill_a", "target": "/opt/skills/a"}])
        svc._create_baas_skill_symlink_conf(bot_uuid="BOT-1", symbol=symbol)

        baas.exec_command_on_bot.assert_called_once()
        cmd = baas.exec_command_on_bot.call_args.kwargs["cmd"]
        assert "skill-symlinks.conf" in cmd
        assert "skill_a" in cmd

    def test_start_baas_sandbox_service_optional_args(self):
        """Lines 511,513,515,519: optional cmd args are appended."""
        baas = MagicMock()
        baas._baas_api_base = "http://baas.local"
        svc = _make_service(baas_service=baas)

        svc._start_baas_sandbox_service(
            bot_uuid="BOT-1",
            client_id="c1",
            engine="aicoding",
            token="tok",
            bot_type="personal",
            bot_id="b123",
            owner_id="u001",
            entity_id="e001",
            entity_type="staff",
            stage="dev",
            admins="admin1",
        )

        cmd = baas.exec_command_on_bot.call_args_list[0].kwargs["cmd"]
        assert "--bot_type personal" in cmd
        assert "--bot_id b123" in cmd
        assert "--owner_id u001" in cmd
        assert "--admins admin1" in cmd

    def test_deserialize_symbol_invalid_json(self):
        """Lines 540-544: invalid JSON returns empty list."""
        from agentclaw.community.core.devices.services.baas_device_service import BaasDeviceService
        result = BaasDeviceService._deserialize_symbol("not valid json{")
        assert result == []


class TestBaasCreateTaskHelpers:
    def test_poll_publish_once_maps_terminal_statuses(self):
        baas = MagicMock()
        svc = _make_service(baas_service=baas)

        baas.get_publish_progress.return_value = {"status": "SUCCESS"}
        assert svc.poll_publish_once(publish_id=1001) == DeviceBindingStatus.ACTIVE.value

        for status in ("FAILED", "REJECTED", "REVOKED"):
            baas.get_publish_progress.return_value = {"status": status}
            assert svc.poll_publish_once(publish_id=1002) == DeviceBindingStatus.FAILED.value

        baas.get_publish_progress.return_value = {"status": "RUNNING"}
        assert svc.poll_publish_once(publish_id=1003) == DeviceBindingStatus.PENDING.value

    def test_poll_publish_once_returns_none_on_transient_error(self):
        baas = MagicMock()
        baas.get_publish_progress.side_effect = BaasServiceError("network")
        svc = _make_service(baas_service=baas)

        assert svc.poll_publish_once(publish_id=1001) is None

    def test_refresh_codefuse_token_skips_when_token_or_bot_uuid_missing(self):
        baas = MagicMock()
        svc = _make_service(baas_service=baas)

        assert svc.refresh_codefuse_token_on_publish_success(
            bot_uuid=None,
            codefuse_token="token",
        ) is None
        assert svc.refresh_codefuse_token_on_publish_success(
            bot_uuid="BOT-1",
            codefuse_token=None,
        ) is None
        baas.exec_command_on_bot.assert_not_called()

    def test_refresh_codefuse_token_returns_decrypt_error(self):
        baas = MagicMock()
        vault = MagicMock()
        vault.decrypt_or_passthrough.side_effect = RuntimeError("bad key")
        svc = _make_service(baas_service=baas, vault=vault)

        result = svc.refresh_codefuse_token_on_publish_success(
            bot_uuid="BOT-1",
            codefuse_token="cipher",
        )

        assert result == "decrypt failed: bad key"
        baas.exec_command_on_bot.assert_not_called()

    def test_refresh_codefuse_token_returns_write_error(self):
        baas = MagicMock()
        vault = TokenVault("master-key-123")
        encrypted = vault.encrypt("plain-token")
        svc = _make_service(baas_service=baas, vault=vault)

        with patch(
            "agentclaw.community.core.devices.services.baas_codefuse_writer.write_codefuse_token_baas",
            side_effect=RuntimeError("exec failed"),
        ) as writer:
            result = svc.refresh_codefuse_token_on_publish_success(
                bot_uuid="BOT-1",
                codefuse_token=encrypted,
            )

        assert result == "write failed: exec failed"
        writer.assert_called_once_with(baas, "BOT-1", "plain-token")

    def test_refresh_codefuse_token_writes_plaintext(self):
        baas = MagicMock()
        vault = TokenVault("master-key-123")
        encrypted = vault.encrypt("plain-token")
        svc = _make_service(baas_service=baas, vault=vault)

        with patch(
            "agentclaw.community.core.devices.services.baas_codefuse_writer.write_codefuse_token_baas",
        ) as writer:
            result = svc.refresh_codefuse_token_on_publish_success(
                bot_uuid="BOT-1",
                codefuse_token=encrypted,
            )

        assert result is None
        writer.assert_called_once_with(baas, "BOT-1", "plain-token")

    def test_run_create_init_once_returns_missing_binding(self):
        repo = MagicMock()
        repo.get_by_id.return_value = None
        svc = _make_service(repo=repo)

        ok, message = svc.run_create_init_once(
            binding_id=42,
            bot_id="bot-001",
            owner_id="owner-001",
            publish_id=1001,
        )

        assert ok is False
        assert "binding not found" in message

    def test_run_create_init_once_rejects_stale_publish_id_and_missing_bot(self):
        repo = MagicMock()
        bot_query = MagicMock()
        svc = _make_service(repo=repo, bot_query=bot_query)
        stale_binding = _make_binding_record(
            id=42,
            device_props={"publish_id": "2002", "bot_uuid": "BOT-1"},
        )
        stale_binding.status = DeviceBindingStatus.PENDING.value
        repo.get_by_id.return_value = stale_binding

        ok, message = svc.run_create_init_once(
            binding_id=42,
            bot_id="bot-001",
            owner_id="owner-001",
            publish_id=1001,
        )

        assert ok is False
        assert "stale publish_id" in message

        missing_bot_binding = _make_binding_record(
            id=42,
            device_props={"publish_id": "1001", "bot_uuid": "BOT-1"},
        )
        missing_bot_binding.status = DeviceBindingStatus.PENDING.value
        repo.get_by_id.return_value = missing_bot_binding
        bot_query.get_by_binding_id.return_value = None

        ok, message = svc.run_create_init_once(
            binding_id=42,
            bot_id="bot-001",
            owner_id="owner-001",
            publish_id=1001,
        )

        assert ok is False
        assert "bot not found" in message

    def test_run_create_init_once_handles_bad_admins_template_config_and_missing_bot_uuid(self):
        repo = MagicMock()
        bot_query = MagicMock()
        svc = _make_service(repo=repo, bot_query=bot_query)
        binding = _make_binding_record(
            id=42,
            device_props={"publish_id": "1001"},
        )
        binding.status = DeviceBindingStatus.PENDING.value
        repo.get_by_id.return_value = binding
        bot_query.get_by_binding_id.return_value = {
            "bot_id": "bot-001",
            "owner_id": "owner-001",
            "active_engine": "",
            "bot_type": "personal",
            "admins": "not-a-list",
            "template_type": "applicationCoding",
            "template_config": "not-a-dict",
        }

        ok, message = svc.run_create_init_once(
            binding_id=42,
            bot_id="bot-001",
            owner_id="owner-001",
            publish_id=1001,
        )

        assert ok is False
        assert message == "missing bot_uuid in device_props"

    def test_run_create_init_once_reports_init_and_alive_failures(self):
        repo = MagicMock()
        bot_query = MagicMock()
        binding = _make_binding_record(
            id=42,
            device_props={
                "publish_id": "1001",
                "bot_uuid": "BOT-1",
                "callback_token": "tok",
            },
        )
        binding.status = DeviceBindingStatus.PENDING.value
        repo.get_by_id.return_value = binding
        bot_query.get_by_binding_id.return_value = {
            "bot_id": "bot-001",
            "owner_id": "owner-001",
            "active_engine": "openclaw",
            "bot_type": "personal",
            "admins": [],
            "template_type": "normal",
            "template_config": {},
        }
        svc = _make_service(repo=repo, bot_query=bot_query)
        svc._run_container_init = MagicMock(side_effect=RuntimeError("init boom"))

        ok, message = svc.run_create_init_once(
            binding_id=42,
            bot_id="bot-001",
            owner_id="owner-001",
            publish_id=1001,
        )

        assert ok is False
        assert "container init failed: init boom" in message

        svc._run_container_init = MagicMock()
        svc.report_device_alive = MagicMock(side_effect=RuntimeError("alive boom"))

        ok, message = svc.run_create_init_once(
            binding_id=42,
            bot_id="bot-001",
            owner_id="owner-001",
            publish_id=1001,
        )

        assert ok is False
        assert "report_device_alive failed: alive boom" in message


class TestAfterBindingPersisted:
    def test_baas_after_binding_persisted_enqueues_create_publish_poll(self):
        task_queue = MagicMock()
        svc = _make_service(task_queue_service=task_queue)
        allocated = AllocatedDevice(
            device_id="BOT-1",
            device_provider=BAAS_DEVICE_PROVIDER,
            device_props={"publish_id": "12372"},
        )

        handled = svc._after_binding_persisted(
            binding_id=1357,
            allocated=allocated,
            bot_id="20260703_demo",
            owner_id="100014",
            device_props={"publish_id": "12372"},
        )

        assert handled is True
        task_queue.enqueue.assert_called_once()
        task_type, payload = task_queue.enqueue.call_args.args[:2]
        assert task_type == BAAS_CREATE_PUBLISH_POLL_TASK
        assert payload["binding_id"] == 1357
        assert payload["bot_id"] == "20260703_demo"
        assert payload["owner_id"] == "100014"
        assert payload["publish_id"] == 12372
        assert "started_at_epoch_s" in payload
        assert task_queue.enqueue.call_args.kwargs["deadline_seconds"] == 86400

    def test_baas_after_binding_persisted_marks_failed_when_task_queue_missing(self):
        svc = _make_service(task_queue_service=None)
        svc._mark_service_start_failed = MagicMock()
        allocated = AllocatedDevice(
            device_id="BOT-1",
            device_provider=BAAS_DEVICE_PROVIDER,
            device_props={"publish_id": "12372"},
        )

        handled = svc._after_binding_persisted(
            binding_id=1357,
            allocated=allocated,
            bot_id="20260703_demo",
            owner_id="100014",
            device_props={"publish_id": "12372"},
        )

        assert handled is True
        svc._mark_service_start_failed.assert_called_once_with(
            binding_id=1357,
            error="enqueue BaaS create publish poll failed: task queue service unavailable",
        )

    def test_baas_after_binding_persisted_marks_failed_when_publish_id_invalid(self):
        task_queue = MagicMock()
        svc = _make_service(task_queue_service=task_queue)
        svc._mark_service_start_failed = MagicMock()
        allocated = AllocatedDevice(
            device_id="BOT-1",
            device_provider=BAAS_DEVICE_PROVIDER,
            device_props={"publish_id": "not-int"},
        )

        handled = svc._after_binding_persisted(
            binding_id=1357,
            allocated=allocated,
            bot_id="20260703_demo",
            owner_id="100014",
            device_props={"publish_id": "not-int"},
        )

        assert handled is True
        task_queue.enqueue.assert_not_called()
        svc._mark_service_start_failed.assert_called_once()
        assert "invalid publish_id" in svc._mark_service_start_failed.call_args.kwargs["error"]

    def test_baas_after_binding_persisted_marks_failed_when_enqueue_raises(self):
        task_queue = MagicMock()
        task_queue.enqueue.side_effect = RuntimeError("queue down")
        svc = _make_service(task_queue_service=task_queue)
        svc._mark_service_start_failed = MagicMock()
        allocated = AllocatedDevice(
            device_id="BOT-1",
            device_provider=BAAS_DEVICE_PROVIDER,
            device_props={"publish_id": "12372"},
        )

        handled = svc._after_binding_persisted(
            binding_id=1357,
            allocated=allocated,
            bot_id="20260703_demo",
            owner_id="100014",
            device_props={"publish_id": "12372"},
        )

        assert handled is True
        svc._mark_service_start_failed.assert_called_once()
        assert "queue down" in svc._mark_service_start_failed.call_args.kwargs["error"]
