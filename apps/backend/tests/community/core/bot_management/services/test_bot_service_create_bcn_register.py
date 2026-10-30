"""All persisted Bots register with BCN before provisioning or early return."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.devices.models import DeviceBindingStatus
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord


def _make_service(*, current_bots: int = 0) -> BotService:
    """构造用于 create_bot BCN 注册测试的最小 BotService。"""
    svc = BotService.__new__(BotService)
    svc._skills_pool_native_creation_policy = MagicMock(
        select=MagicMock(return_value=None)
    )
    svc._skill_layout_repository = MagicMock()
    svc._bot_storage_policy = None
    svc._bot_app_grant_provider = lambda: MagicMock()
    svc._repository = MagicMock()
    svc._repository.count_by_owner.return_value = current_bots
    svc._repository.get_by_id_and_owner.return_value = None
    svc._repository.exists_by_bot_name.return_value = False

    def _insert_with_layout(data, *, layout):
        record = {"id": 1, **data}
        svc._repository.get_by_id_and_owner.return_value = record
        return record

    svc._repository.insert_with_initial_skill_layout.side_effect = _insert_with_layout
    svc._repository.claim_provisioning.return_value = True
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
    svc._workspace_hosting_config = MagicMock(aixcore_base_url="")

    skill_set_service = MagicMock()
    skill_set_service.get_symlink_mappings.return_value = []
    svc._skill_set_factory = MagicMock()
    svc._skill_set_factory.create.return_value = skill_set_service

    publish_service = MagicMock()
    publish_service.create_publish.return_value = MagicMock(
        to_dict=lambda: {"publish_id": "p1"}
    )
    svc._bot_publish_provider = lambda: publish_service

    teclaw_provision = MagicMock()
    teclaw_provision.is_teclaw.return_value = False
    svc._teclaw_provision_provider = lambda: teclaw_provision
    svc._common_config_service = None
    svc._policy_service = None
    # DRM reader: default unset (None) ⇒ _is_new_bot_use_nas() is False (OSS).
    # BCN-register tests patch BotService._is_claude_code_bcn_register_enabled.
    svc._drm_reader = MagicMock()
    svc._drm_reader.read.return_value = None

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
class TestCreateBotBcnRegister:
    """create_bot 中 BCN Provider 注册逻辑测试。"""

    def test_claude_code_normalcc_personal_triggers_bcn_register(self):
        """claude_code + normalCC personal bot 创建时应触发 BCN 注册。"""
        svc = _make_service()
        _attach_device_service(svc)
        svc._bcn_service.register_provider_bot.return_value = {
            "bot_uuid": "u1",
            "bot_runtime_token": "tok",
        }

        with patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=True):
            svc.create_bot(
                user_id="u1",
                nick_name="nick",
                bot_name="cc-bot",
                bot_id="cc-1",
                engine_type="claude_code",
                bot_type="personal",
                template_type="normalCC",
            )

        svc._bcn_service.register_provider_bot.assert_called_once_with(
            teamclaw_bot_uuid="cc-1",
            owner_workno="u1",
            name="cc-bot",
            summary="",
        )

    def test_claude_code_normalcc_service_triggers_bcn_register(self):
        """claude_code + normalCC service bot 创建时应触发 BCN 注册。"""
        svc = _make_service()
        _attach_device_service(svc)
        svc._bcn_service.register_provider_bot.return_value = {
            "bot_uuid": "u1",
            "bot_runtime_token": "tok",
        }

        with patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=True):
            svc.create_bot(
                user_id="u1",
                nick_name="nick",
                bot_name="cc-svc-bot",
                bot_id="cc-svc-1",
                engine_type="claude_code",
                bot_type="service",
                template_type="normalCC",
                bot_desc="service desc",
            )

        svc._bcn_service.register_provider_bot.assert_called_once_with(
            teamclaw_bot_uuid="cc-svc-1",
            provider_bot_ref="cc-svc-1:staff_u1",
            owner_workno="u1",
            name="cc-svc-bot",
            summary="service desc",
        )

    def test_service_bot_create_uses_default_image_and_persists_marker(self):
        svc = _make_service()
        device_service = _attach_device_service(svc)
        common_config = MagicMock()
        common_config.get_value.return_value = {"image": "registry/arca:v2"}
        svc._common_config_service = common_config
        template_config = {
            "image": "registry/arca:v1",
            "envs": {"A": "1"},
        }

        svc.create_bot(
            user_id="u1",
            nick_name="nick",
            bot_name="pinned-service-bot",
            bot_id="service-pin-1",
            engine_type="openclaw",
            bot_type="service",
            template_type="service",
            template_config=template_config,
            ext={"service_bot_config": {"device_count": 3}},
        )

        inserted_ext = svc._repository.insert_with_initial_skill_layout.call_args.args[0]["ext"]
        assert inserted_ext == {
            "service_bot_config": {"device_count": 3},
            "sbot_use_default_image": True,
        }
        apply_kwargs = device_service.apply_device.call_args.kwargs
        assert apply_kwargs["template_config"] == {
            "image": "registry/arca:v1",
            "envs": {"A": "1"},
        }
        assert template_config["image"] == "registry/arca:v1"
        common_config.get_value.assert_called_once()

    @pytest.mark.parametrize(
        "config_value",
        [None, {}, {"image": ""}],
        ids=["missing-or-disabled", "missing-image", "empty-image"],
    )
    def test_service_bot_create_without_active_image_policy_has_no_markers(
        self, config_value
    ):
        svc = _make_service()
        _attach_device_service(svc)
        common_config = MagicMock()
        common_config.get_value.return_value = config_value
        svc._common_config_service = common_config

        svc.create_bot(
            user_id="u1",
            nick_name="nick",
            bot_name="legacy-service-bot",
            bot_id="service-legacy-1",
            engine_type="openclaw",
            bot_type="service",
            template_type="service",
            ext={
                "service_bot_config": {"device_count": 3},
                "sbot_use_default_image": True,
                "sbot_pin_image": True,
                "sbot_docker_image": "stale:v1",
            },
        )

        inserted_ext = svc._repository.insert_with_initial_skill_layout.call_args.args[0]["ext"]
        assert inserted_ext == {"service_bot_config": {"device_count": 3}}
        common_config.get_value.assert_called_once()

    def test_claude_code_application_coding_triggers_bcn_register(self):
        """Application templates must not suppress registration."""
        svc = _make_service()
        _attach_device_service(svc)

        with patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=True):
            svc.create_bot(
                user_id="u1",
                nick_name="nick",
                bot_name="cc-app-bot",
                bot_id="cc-app-1",
                engine_type="claude_code",
                bot_type="personal",
                template_type="applicationCoding",
            )

        svc._bcn_service.register_provider_bot.assert_called_once()

    def test_claude_code_personal_coding_triggers_bcn_register(self):
        """claude_code + personalCoding 创建时应触发 BCN 注册。"""
        svc = _make_service()
        _attach_device_service(svc)
        svc._bcn_service.register_provider_bot.return_value = {
            "bot_uuid": "u1",
            "bot_runtime_token": "tok",
        }

        with patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=True):
            svc.create_bot(
                user_id="u1",
                nick_name="nick",
                bot_name="cc-personal-coding-bot",
                bot_id="cc-personal-coding-1",
                engine_type="claude_code",
                bot_type="personal",
                template_type="personalCoding",
            )

        svc._bcn_service.register_provider_bot.assert_called_once_with(
            teamclaw_bot_uuid="cc-personal-coding-1",
            owner_workno="u1",
            name="cc-personal-coding-bot",
            summary="",
        )

    def test_aicoding_personal_coding_triggers_bcn_register(self):
        """aicoding + personalCoding 创建时应触发 BCN 注册。"""
        svc = _make_service()
        _attach_device_service(svc)
        svc._bcn_service.register_provider_bot.return_value = {
            "bot_uuid": "u1",
            "bot_runtime_token": "tok",
        }

        with patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=True):
            svc.create_bot(
                user_id="u1",
                nick_name="nick",
                bot_name="aicoding-personal-coding-bot",
                bot_id="aicoding-personal-coding-1",
                engine_type="aicoding",
                bot_type="personal",
                template_type="personalCoding",
            )

        svc._bcn_service.register_provider_bot.assert_called_once_with(
            teamclaw_bot_uuid="aicoding-personal-coding-1",
            owner_workno="u1",
            name="aicoding-personal-coding-bot",
            summary="",
        )

    @pytest.mark.parametrize(
        ("template_type", "template_type_state"),
        [(None, "none"), ("", "empty")],
        ids=["none", "empty"],
    )
    def test_claude_code_missing_template_type_triggers_bcn_register(
        self,
        template_type: str | None,
        template_type_state: str,
        caplog: pytest.LogCaptureFixture,
    ):
        """claude_code + 缺失 template_type 与 normalCC 等价。"""
        caplog.set_level("INFO")
        svc = _make_service()
        _attach_device_service(svc)
        svc._bcn_service.register_provider_bot.return_value = {
            "bot_uuid": "u1",
            "bot_runtime_token": "tok",
        }

        with patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=True):
            svc.create_bot(
                user_id="u1",
                nick_name="nick",
                bot_name="cc-no-template",
                bot_id="cc-missing-template-1",
                engine_type="claude_code",
                bot_type="personal",
                template_type=template_type,
            )

        svc._bcn_service.register_provider_bot.assert_called_once_with(
            teamclaw_bot_uuid="cc-missing-template-1",
            owner_workno="u1",
            name="cc-no-template",
            summary="",
        )
        assert "register bot to BCN as provider" in caplog.text

    def test_teclaw_personal_triggers_bcn_register(self):
        """teclaw 引擎 personal bot 创建时应触发 BCN 注册（与 claude_code 一致）。"""
        svc = _make_service()
        _attach_device_service(svc)
        svc._bcn_service.register_provider_bot.return_value = {
            "bot_uuid": "u1",
            "bot_runtime_token": "tok",
        }

        with patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=True):
            svc.create_bot(
                user_id="u1",
                nick_name="nick",
                bot_name="tc-bot",
                bot_id="tc-1",
                engine_type="teclaw",
                bot_type="personal",
            )

        svc._bcn_service.register_provider_bot.assert_called_once_with(
            teamclaw_bot_uuid="tc-1",
            owner_workno="u1",
            name="tc-bot",
            summary="",
        )

    def test_openclaw_service_triggers_bcn_register(self):
        """openclaw 引擎 service bot 创建时应触发 BCN 注册。"""
        svc = _make_service()
        _attach_device_service(svc)
        svc._bcn_service.register_provider_bot.return_value = {
            "bot_uuid": "u1",
            "bot_runtime_token": "tok",
        }

        with patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=True):
            svc.create_bot(
                user_id="u1",
                nick_name="nick",
                bot_name="oc-svc-bot",
                bot_id="oc-svc-1",
                engine_type="openclaw",
                bot_type="service",
                bot_desc="oc svc desc",
            )

        svc._bcn_service.register_provider_bot.assert_called_once_with(
            teamclaw_bot_uuid="oc-svc-1",
            provider_bot_ref="oc-svc-1:staff_u1",
            owner_workno="u1",
            name="oc-svc-bot",
            summary="oc svc desc",
        )

    def test_openclaw_personal_registers_plugin_before_device_allocation(self):
        """OpenClaw personal creation registers plugin mode before apply_device."""
        svc = _make_service()
        device_service = _attach_device_service(svc)
        timeline = []
        device_result = _device_result()
        device_service.apply_device.side_effect = lambda **kwargs: (
            timeline.append(("apply_device", kwargs)), device_result
        )[1]
        svc._bcn_service.register_provider_bot.side_effect = lambda **kwargs: (
            timeline.append(("register", kwargs)), {"bot_uuid": "u1"}
        )[1]

        with patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=True):
            svc.create_bot(
                user_id="u1",
                nick_name="nick",
                bot_name="oc-personal",
                bot_id="oc-p-1",
                engine_type="openclaw",
                bot_type="personal",
            )

        assert [name for name, _ in timeline[:2]] == ["register", "apply_device"]
        assert timeline[0][1]["connection_mode"] == "plugin"

    @pytest.mark.parametrize("capabilities", [{"bcn": {"join_as_provider": False}}, {}])
    def test_create_ignores_disabled_or_missing_bcn_capability(self, capabilities):
        svc = _make_service()
        _attach_device_service(svc)
        svc.create_bot(
            user_id="u1", nick_name="nick", bot_id="factory-bot", engine_type="openclaw",
            bot_type="personal", template_type="personal",
            template_config={"template_key": "openclaw-personal", "capabilities": capabilities},
        )
        svc._bcn_service.register_provider_bot.assert_called_once()

    def test_moltis_engine_triggers_bcn_register(self):
        """Moltis also registers with the default gateway mode."""
        svc = _make_service()
        _attach_device_service(svc)

        with patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=True):
            svc.create_bot(
                user_id="u1",
                nick_name="nick",
                bot_name="moltis-bot",
                bot_id="m-1",
                engine_type="moltis",
                bot_type="personal",
            )

        svc._bcn_service.register_provider_bot.assert_called_once()

    def test_bcn_register_failure_does_not_block_create(self):
        """BCN 注册失败不阻塞 create_bot 主流程。"""
        svc = _make_service()
        _attach_device_service(svc)
        svc._bcn_service.register_provider_bot.side_effect = RuntimeError("bcn down")

        with patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=True):
            result = svc.create_bot(
                user_id="u1",
                nick_name="nick",
                bot_name="cc-bot-fail",
                bot_id="cc-fail-1",
                engine_type="claude_code",
                bot_type="personal",
                template_type="normalCC",
            )

        assert result["bot_id"] == "cc-fail-1"
        assert result["status"] == "ACTIVE"

    def test_drm_disabled_still_registers(self):
        """Lifecycle registration bypasses the legacy DRM switch."""
        svc = _make_service()
        _attach_device_service(svc)

        with patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=False):
            svc.create_bot(
                user_id="u1",
                nick_name="nick",
                bot_name="cc-drm-off",
                bot_id="cc-drm-1",
                engine_type="claude_code",
                bot_type="personal",
                template_type="normalCC",
            )

        svc._bcn_service.register_provider_bot.assert_called_once()


@pytest.mark.parametrize("engine", ["hermes", "moltis", "deepseek_harness", "claude_code", "aicoding", "teclaw", "openclaw"])
@pytest.mark.parametrize("bot_type", ["personal", "desktop", "service"])
def test_deferred_create_registers_all_engine_types_once(engine, bot_type):
    svc = _make_service()
    device_service = _attach_device_service(svc)
    svc._bcn_service.register_provider_bot.return_value = {"bot_uuid": "bcn-1"}
    with patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=False):
        record = svc.create_bot(
            user_id="u1", nick_name="nick", bot_name="all-bots", bot_desc="description",
            bot_id="all-1", engine_type=engine, bot_type=bot_type, provision=False,
        )
    assert record["bot_id"] == "all-1"
    expected = dict(teamclaw_bot_uuid="all-1", owner_workno="u1", name="all-bots", summary="description")
    if bot_type == "service":
        expected["provider_bot_ref"] = "all-1:staff_u1"
    if engine == "openclaw" and bot_type in ("personal", "desktop"):
        expected["connection_mode"] = "plugin"
    svc._bcn_service.register_provider_bot.assert_called_once_with(**expected)
    svc._repository.update_by_owner.assert_called_with(
        "all-1", "u1", {"ext": {"bcn_registered": True}},
    )
    device_service.apply_device.assert_not_called()
    if bot_type != "desktop":
        svc.provision_bot(bot_id="all-1", user_id="u1", nick_name="nick")
        svc._bcn_service.register_provider_bot.assert_called_once_with(**expected)


def test_deferred_create_environment_skip_does_not_mark_registered():
    svc = _make_service()
    svc._bcn_service.register_provider_bot.return_value = {"skipped": True}
    record = svc.create_bot(user_id="u1", nick_name="nick", bot_id="skip-1", engine_type="hermes", provision=False)
    assert record["bot_id"] == "skip-1"
    svc._repository.update_by_owner.assert_not_called()
