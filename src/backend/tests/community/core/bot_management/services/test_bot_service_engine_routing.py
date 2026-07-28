"""Bot service 引擎路由测试 — create_bot 的 engine 参数传递。

覆盖 openspec/changes/archive/2026-05-25-service-bot-claudecode-engine
spec engine-aware-baas 中的：
- "Service Bot supports claude_code engine creation"

该测试聚焦于 create_bot() 中传给 apply_device 的 engine 值：
- claude_code 引擎统一传 claude_code（不再路由为 aicoding）
- 下游通过 template_type 兜底判断，无需上层路由
- openclaw service bot 行为不变
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.devices.models import DeviceBindingStatus
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord


def _make_service(*, current_bots: int = 0) -> BotService:
    """构造一个仅用于 create_bot 路由测试的最小 BotService。"""
    svc = BotService.__new__(BotService)
    svc._repository = MagicMock()
    svc._repository.count_by_owner.return_value = current_bots
    svc._repository.get_by_id_and_owner.return_value = None
    svc._repository.exists_by_bot_name.return_value = False
    svc._repository.insert.side_effect = lambda data: {"id": 1, **data}
    svc._repository.update_by_owner.return_value = None
    svc._repository.soft_delete_by_owner.return_value = None

    svc._allocation_config = SimpleNamespace(mode="multi", max_devices_per_entity=10)
    svc._passport_plugin = MagicMock()
    svc._oss_record_repo = MagicMock()
    svc._device_binding_repo = MagicMock()
    svc._device_binding_repo.list_by_owner.return_value = []
    svc._cleanup_service = MagicMock()
    svc._bcn_service = MagicMock()
    svc._bot_publish_repo = MagicMock()
    svc._template_service = MagicMock()
    svc._workspace_hosting_service = MagicMock()
    svc._workspace_hosting_config = MagicMock(aixcore_base_url="", aixcore_base_url_pre="")

    skill_set_service = MagicMock()
    skill_set_service.get_symlink_mappings.return_value = []
    svc._skill_set_factory = MagicMock()
    svc._skill_set_factory.create.return_value = skill_set_service

    # publish service 仅在 service bot 流程被调用
    publish_service = MagicMock()
    publish_service.create_publish.return_value = MagicMock(
        to_dict=lambda: {"publish_id": "p1"}
    )
    svc._bot_publish_provider = lambda: publish_service

    # These routing tests use non-teclaw engines; the teclaw provision branch
    # must not fire (is_teclaw -> False).
    teclaw_provision = MagicMock()
    teclaw_provision.is_teclaw.return_value = False
    svc._teclaw_provision_provider = lambda: teclaw_provision
    svc._policy_service = None
    # DRM reader: default unset (None) ⇒ _is_new_bot_use_nas() is False (OSS).
    svc._drm_reader = MagicMock()
    svc._drm_reader.read.return_value = None
    svc._baas_template_resolver = None

    return svc


def _device_result() -> DeviceBindingRecord:
    return DeviceBindingRecord(
        id=1,
        entity_id="u1",
        entity_type="staff",
        device_id="dev-1",
        device_provider="arca",
        env="dev",
        device_props={},
        status=DeviceBindingStatus.ACTIVE.value,
        apply_reason=None,
        applied_by="u1",
        release_reason=None,
        released_by=None,
        released_at=None,
        last_alive_at=None,
        gmt_create=None,
        gmt_modified=None,
    )


def _attach_device_service(svc: BotService) -> MagicMock:
    device_service = MagicMock()
    device_service.apply_device.return_value = _device_result()
    svc._device_service_provider = lambda: device_service
    return device_service


@pytest.mark.unit
class TestCreateBotEngineRouting:
    def test_service_bot_claude_code_keeps_claude_code_engine(self):
        svc = _make_service()
        device_service = _attach_device_service(svc)

        svc.create_bot(
            user_id="u1",
            nick_name="nick",
            bot_name="srv-bot-1",
            bot_id="srv-1",
            engine_type="claude_code",
            bot_type="service",
            template_type=None,
        )

        assert device_service.apply_device.called
        _, kwargs = device_service.apply_device.call_args
        assert kwargs["engine"] == "claude_code"

    def test_service_bot_with_claude_code_template_type_keeps_claude_code(self):
        # spec: template_type=="claude_code" (service bot scenario) 仍保留 claude_code
        svc = _make_service()
        device_service = _attach_device_service(svc)

        svc.create_bot(
            user_id="u1",
            nick_name="nick",
            bot_name="srv-bot-2",
            bot_id="srv-2",
            engine_type="claude_code",
            bot_type="service",
            template_type="claude_code",
        )

        _, kwargs = device_service.apply_device.call_args
        assert kwargs["engine"] == "claude_code"

    def test_personal_bot_with_application_coding_template_keeps_claude_code(self):
        svc = _make_service()
        device_service = _attach_device_service(svc)

        svc.create_bot(
            user_id="u1",
            nick_name="nick",
            bot_name="coding-bot",
            bot_id="cod-1",
            engine_type="claude_code",
            bot_type="personal",
            template_type="applicationCoding",
        )

        _, kwargs = device_service.apply_device.call_args
        assert kwargs["engine"] == "claude_code"

    def test_omitted_bot_type_is_resolved_before_device_routing(self):
        svc = _make_service()
        device_service = _attach_device_service(svc)

        svc.create_bot(
            user_id="u1",
            nick_name="nick",
            bot_name="personal-default",
            bot_id="personal-default-1",
            engine_type="openclaw",
        )

        _, kwargs = device_service.apply_device.call_args
        assert kwargs["bot_type"] == "personal"

    def test_resolves_template_uid_before_apply_device(self):
        svc = _make_service()
        device_service = _attach_device_service(svc)
        resolver = MagicMock()
        resolver.resolve_template_uid.return_value = "openclaw_personal_default"
        svc._baas_template_resolver = resolver

        svc.create_bot(
            user_id="u1",
            nick_name="nick",
            bot_name="personal-baas",
            bot_id="personal-baas-1",
            engine_type="openclaw",
            bot_type="personal",
            template_type="normalCC",
        )

        resolver.resolve_template_uid.assert_called_once_with(
            bot_id="personal-baas-1",
            user_id="u1",
            env="dev",
            bot_type="personal",
            engine_type="openclaw",
            template_type="normalCC",
            template_config=None,
        )
        _, kwargs = device_service.apply_device.call_args
        assert kwargs["template_config"] == {
            "template_uid": "openclaw_personal_default"
        }

    def test_template_uid_resolution_failure_does_not_block_upstream_arca_route(self):
        svc = _make_service()
        device_service = _attach_device_service(svc)
        resolver = MagicMock()
        resolver.resolve_template_uid.side_effect = RuntimeError("selector missing")
        svc._baas_template_resolver = resolver

        svc.create_bot(
            user_id="u1",
            nick_name="nick",
            bot_name="personal-arca",
            bot_id="personal-arca-1",
            engine_type="openclaw",
            bot_type="personal",
        )

        resolver.resolve_template_uid.assert_called_once()
        _, kwargs = device_service.apply_device.call_args
        assert kwargs["template_config"] == {
            "template_uid": None,
            "_baas_template_uid_resolution_error": "selector missing",
        }

    def test_personal_bot_with_personal_coding_template_keeps_claude_code(self):
        svc = _make_service()
        device_service = _attach_device_service(svc)

        svc.create_bot(
            user_id="u1",
            nick_name="nick",
            bot_name="coding-bot-p",
            bot_id="cod-2",
            engine_type="claude_code",
            bot_type="personal",
            template_type="personalCoding",
        )

        _, kwargs = device_service.apply_device.call_args
        assert kwargs["engine"] == "claude_code"

    def test_service_bot_openclaw_unchanged(self):
        svc = _make_service()
        device_service = _attach_device_service(svc)

        svc.create_bot(
            user_id="u1",
            nick_name="nick",
            bot_name="srv-oc",
            bot_id="srv-oc-1",
            engine_type="openclaw",
            bot_type="service",
        )

        _, kwargs = device_service.apply_device.call_args
        assert kwargs["engine"] == "openclaw"

    def test_personal_teclaw_bot_provisions_via_baas_not_apply_device(self):
        svc = _make_service()
        device_service = _attach_device_service(svc)
        teclaw = MagicMock()
        teclaw.is_teclaw.return_value = True
        teclaw.provision.return_value = SimpleNamespace(
            binding_id=5, device_id="BOT-x", status="PENDING",
            config_artifact={"schema_version": 4},
        )
        svc._teclaw_provision_provider = lambda: teclaw

        svc.create_bot(
            user_id="u1",
            nick_name="nick",
            bot_name="t-bot",
            bot_id="t-1",
            engine_type="teclaw",
            bot_type="personal",
        )

        # Personal teclaw bot eager-provisions via BaaS, not DeviceService.
        teclaw.provision.assert_called_once()
        assert not device_service.apply_device.called
        # Personal bots have no publish row — nothing to record onto.
        svc._bot_publish_provider().record_draft_artifact.assert_not_called()

    def test_personal_teclaw_bot_provisions_without_a_passport_token(self):
        # The AgentPass token is fetched and pushed by the create publish poll
        # task once the container is up — create never needs it, so a passport
        # outage cannot affect provisioning.
        svc = _make_service()
        device_service = _attach_device_service(svc)
        svc._passport_plugin.query_token.side_effect = RuntimeError("boom")
        teclaw = MagicMock()
        teclaw.is_teclaw.return_value = True
        teclaw.provision.return_value = SimpleNamespace(
            binding_id=5, device_id="BOT-x", status="PENDING",
            config_artifact={"schema_version": 4},
        )
        svc._teclaw_provision_provider = lambda: teclaw

        svc.create_bot(
            user_id="u1",
            nick_name="nick",
            bot_name="t-bot-token-fail",
            bot_id="t-token-fail-1",
            engine_type="teclaw",
            bot_type="personal",
        )

        teclaw.provision.assert_called_once()
        assert "agent_pass_token" not in teclaw.provision.call_args.kwargs
        assert not device_service.apply_device.called

    def test_service_teclaw_bot_provisions_eagerly_and_creates_publish(self):
        svc = _make_service()
        device_service = _attach_device_service(svc)
        teclaw = MagicMock()
        teclaw.is_teclaw.return_value = True
        _art = {"schema_version": 4, "skills": [], "mcp": {"servers": []}}
        teclaw.provision.return_value = SimpleNamespace(
            binding_id=5, device_id="BOT-x", status="PENDING",
            config_artifact=_art,
        )
        svc._teclaw_provision_provider = lambda: teclaw

        svc.create_bot(
            user_id="u1",
            nick_name="nick",
            bot_name="t-srv",
            bot_id="t-srv-1",
            engine_type="teclaw",
            bot_type="service",
        )

        # A teclaw service bot now eager-provisions its draft container via BaaS
        # (no apply_device) AND still creates the publish record — the eager branch
        # no longer returns early; both paths converge on the shared tail.
        teclaw.provision.assert_called_once()
        assert not device_service.apply_device.called
        svc._bot_publish_provider().create_publish.assert_called_once()
        # the initial provisioned artifact is recorded onto the new draft row
        svc._bot_publish_provider().record_draft_artifact.assert_called_once_with(
            bot_id="t-srv-1", artifact=_art,
        )

    def test_service_teclaw_draft_artifact_record_failure_is_swallowed(self):
        # Recording the draft artifact is best-effort: a recorder error must
        # not fail bot creation — the publish row was already created.
        svc = _make_service()
        _attach_device_service(svc)
        teclaw = MagicMock()
        teclaw.is_teclaw.return_value = True
        teclaw.provision.return_value = SimpleNamespace(
            binding_id=5, device_id="BOT-x", status="PENDING",
            config_artifact={"schema_version": 4, "skills": []},
        )
        svc._teclaw_provision_provider = lambda: teclaw
        svc._bot_publish_provider().record_draft_artifact.side_effect = (
            RuntimeError("boom")
        )

        bot_record = svc.create_bot(
            user_id="u1",
            nick_name="nick",
            bot_name="t-srv-err",
            bot_id="t-srv-err-1",
            engine_type="teclaw",
            bot_type="service",
        )

        # Creation still succeeds and the publish row is intact.
        assert bot_record["bot_id"] == "t-srv-err-1"
        svc._bot_publish_provider().create_publish.assert_called_once()
        svc._bot_publish_provider().record_draft_artifact.assert_called_once()


@pytest.mark.unit
class TestGetBotStatusNoReadThrough:
    """get_bot returns the stored status as-is for every engine — the teclaw
    read-through is gone (the TeclawPublishTaskHandler keeps the stored column
    fresh post-provision), so the DB value is authoritative and baas is never
    probed on a detail read."""

    @staticmethod
    def _bot_row(*, status="PENDING", binding_id=5):
        return {
            "bot_id": "b1",
            "owner_id": "u1",
            "status": status,
            "binding_id": binding_id,
            "device_id": "BOT-x",
        }

    def test_teclaw_status_returned_from_stored_column(self) -> None:
        svc = _make_service()
        _attach_device_service(svc)
        svc._repository.get_by_id_and_owner.return_value = self._bot_row(status="PENDING")
        teclaw = MagicMock()
        svc._teclaw_provision_provider = lambda: teclaw

        bot = svc.get_bot("b1", "u1")

        # Stored status passes through unchanged — no baas read-through.
        assert bot["status"] == "PENDING"
        teclaw.get_live_status_by_binding_id.assert_not_called()

    def test_active_stored_status_returned_unchanged(self) -> None:
        svc = _make_service()
        _attach_device_service(svc)
        svc._repository.get_by_id_and_owner.return_value = self._bot_row(status="ACTIVE")
        teclaw = MagicMock()
        svc._teclaw_provision_provider = lambda: teclaw

        bot = svc.get_bot("b1", "u1")

        assert bot["status"] == "ACTIVE"
        teclaw.get_live_status_by_binding_id.assert_not_called()
