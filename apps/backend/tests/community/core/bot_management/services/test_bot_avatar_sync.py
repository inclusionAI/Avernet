"""Task 3 daily registration/edit avatar trigger and projection tests.

覆盖：
- ``avatar_from_record`` 数据来源优先级 —— 明确空串不被 truthiness 回退掉；
- ``update_bot`` 的头像触发矩阵 —— 只改头像（设置/清空）、名称/描述连带头像、
  无关 ext、``sync_to_bcn=False``、本地写失败逐项断言；
- ``_sync_bot_to_bcn`` 从已保存记录投影头像，skipped / ``onboarded=false``
  记录非成功日志，读取失败不伪造空串头像；
- 注册 helper 的 ``avatar_url`` 边界参数三态（发送 / 清空 / 省略），以及
  start_bot 真实调用链按相同条件透传投影。
"""

from unittest.mock import MagicMock, Mock, patch

import pytest

from agentclaw.community.core.bot_management.collaboration_repair.profile import (
    avatar_from_record,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
    BotService,
    BotServiceError,
)
from agentclaw.community.core.bot_management.services.bcn_service import BcnServiceError


def _make_bot_service(repository=None, device_service=None, bcn_service=None) -> BotService:
    """Build a BotService with MagicMock fallbacks for the injected deps
    not under test (DI ctor requires all of them)."""
    return BotService(
        caller_identity_repo=MagicMock(),
        drm_reader=MagicMock(),
        repository=repository or Mock(),
        allocation_config=MagicMock(),
        device_binding_repo=MagicMock(),
        skill_set_factory=MagicMock(),
        cleanup_service=MagicMock(),
        bcn_service=bcn_service or MagicMock(),
        bot_publish_repo=MagicMock(),
        passport_plugin=MagicMock(),
        oss_record_repo=MagicMock(),
        bot_publish_service_provider=lambda: MagicMock(),
        device_service_provider=(lambda: device_service if device_service is not None else MagicMock()),
        bot_app_grant_service_provider=lambda: MagicMock(),
        path_factory=MagicMock(),
        template_service=MagicMock(),
        workspace_hosting_service=MagicMock(),
        collaborator_repo=MagicMock(),
        restart_lock_repo=MagicMock(),
        teclaw_provision_service_provider=lambda: MagicMock(is_teclaw=MagicMock(return_value=False)),
        device_status_client=MagicMock(),
        cron_auto_setup_service_provider=lambda: MagicMock(),
        skills_pool_native_creation_policy=MagicMock(
            select=MagicMock(return_value=None)
        ),
        skill_layout_repository=MagicMock(),
    )


def _resolver_repo(record: dict) -> Mock:
    """Repository mock whose address resolver re-read yields ``record``."""
    repo = Mock()
    repo.get_by_id_and_owner.return_value = record
    repo.update_by_owner.return_value = record
    repo.list_matches_by_legacy_locator.return_value = [
        {"id": 1, "entity_id": record["owner_id"], **record}
    ]
    return repo


class TestAvatarSourcePreservesClear:
    """`avatar_from_record` 数据来源优先级（计划给定的 RED 矩阵）。"""

    @pytest.mark.parametrize(
        "bot, expected",
        [
            ({"ext": {"avatar_url": "https://example.com/a.png"}}, "https://example.com/a.png"),
            ({"avatar_url": "", "ext": {"avatar_url": "old"}}, ""),
            ({"avatar_url": None, "ext": {"avatar_url": "legacy"}}, "legacy"),
            ({"ext": {"avatar_url": ""}}, ""),
            ({"ext": {}}, ""),
        ],
    )
    def test_avatar_source_preserves_clear(self, bot, expected):
        assert avatar_from_record(bot) == expected


class TestAvatarSourceStrictness:
    """明确存在但非法的字段必须报错；原始 JSON 字符串必须结构化解析。"""

    def test_top_level_non_string_is_rejected(self):
        with pytest.raises(ValueError, match="avatar_url"):
            avatar_from_record({"avatar_url": 123})

    def test_ext_non_string_is_rejected(self):
        with pytest.raises(ValueError, match="avatar_url"):
            avatar_from_record({"ext": {"avatar_url": 123}})

    def test_ext_none_entry_is_rejected(self):
        with pytest.raises(ValueError, match="avatar_url"):
            avatar_from_record({"ext": {"avatar_url": None}})

    def test_raw_json_ext_is_structure_parsed(self):
        bot = {
            "ext": '{"avatar_url": "https://example.com/a.png", "bcn_registered": true}'
        }
        assert avatar_from_record(bot) == "https://example.com/a.png"

    def test_corrupt_ext_json_is_rejected(self):
        with pytest.raises(ValueError, match="corrupt JSON"):
            avatar_from_record({"ext": "{"})

    def test_ext_json_decoding_to_non_object_is_rejected(self):
        with pytest.raises(ValueError, match="must decode to an object"):
            avatar_from_record({"ext": "[1, 2]"})

    def test_ext_of_illegal_container_type_is_rejected(self):
        with pytest.raises(ValueError, match="must be an object"):
            avatar_from_record({"ext": 7})

    def test_absent_everything_projects_explicit_empty(self):
        assert avatar_from_record({"ext": None}) == ""


class TestUpdateBotAvatarSyncTrigger:
    """`update_bot` 的头像触发矩阵。"""

    @pytest.fixture
    def mock_repository(self):
        repo = Mock()
        repo.get_by_id_and_owner.return_value = {
            "bot_id": "test-bot-id",
            "bot_name": "Old Name",
            "bot_desc": "Old Description",
            "owner_id": "85020",
            "status": "ACTIVE",
            "binding_id": None,
            "ext": {},
        }
        repo.update_by_owner.return_value = {
            "bot_id": "test-bot-id",
            "bot_name": "Old Name",
            "bot_desc": "Old Description",
            "owner_id": "85020",
            "status": "ACTIVE",
            "binding_id": None,
            "ext": {"avatar_url": "https://example.com/saved.png"},
        }
        repo.get_by_bot_name.return_value = None
        return repo

    def test_avatar_only_edit_triggers_bcn_sync(self, mock_repository):
        """只改头像（设置）也必须触发 BCN 同步 —— 不再只看 name/desc。"""
        service = _make_bot_service(repository=mock_repository)

        with patch.object(service, "_sync_bot_to_bcn") as mock_sync:
            service.update_bot(
                bot_id="test-bot-id",
                user_id="85020",
                ext={"avatar_url": "https://example.com/saved.png"},
            )

        mock_sync.assert_called_once_with(
            bot_id="test-bot-id",
            owner_id="85020",
            bot_name=None,
            bot_desc=None,
        )

    def test_avatar_only_clear_triggers_bcn_sync(self, mock_repository):
        """只清空头像（ext 显式空串）必须触发 BCN 同步。"""
        service = _make_bot_service(repository=mock_repository)

        with patch.object(service, "_sync_bot_to_bcn") as mock_sync:
            service.update_bot(
                bot_id="test-bot-id",
                user_id="85020",
                ext={"avatar_url": ""},
            )

        mock_sync.assert_called_once_with(
            bot_id="test-bot-id",
            owner_id="85020",
            bot_name=None,
            bot_desc=None,
        )

    def test_avatar_edit_alongside_name_and_desc_triggers_bcn_sync(self, mock_repository):
        """名称/描述连带头像照常触发，且地址、请求认证透传保持原样。"""
        service = _make_bot_service(repository=mock_repository)
        request_headers = {"authorization": "Bearer opaque-or-jwt-token"}

        with patch.object(service, "_sync_bot_to_bcn") as mock_sync:
            service.update_bot(
                bot_id="test-bot-id",
                user_id="85020",
                bot_name="New Name",
                bot_desc="New Description",
                ext={"avatar_url": "https://example.com/saved.png"},
                request_headers=request_headers,
            )

        mock_sync.assert_called_once_with(
            bot_id="test-bot-id",
            owner_id="85020",
            bot_name="New Name",
            bot_desc="New Description",
            request_headers=request_headers,
        )

    def test_unrelated_ext_only_does_not_trigger_bcn_sync(self, mock_repository):
        """无关 ext 键不构成头像变更，不得触发同步。"""
        service = _make_bot_service(repository=mock_repository)

        with patch.object(service, "_sync_bot_to_bcn") as mock_sync:
            service.update_bot(
                bot_id="test-bot-id",
                user_id="85020",
                ext={"read_only_rules": []},
            )

        mock_sync.assert_not_called()

    def test_avatar_only_edit_respects_sync_to_bcn_false(self, mock_repository):
        """`sync_to_bcn=False` 对头像-only 编辑同样完全压制同步。"""
        service = _make_bot_service(repository=mock_repository)

        with patch.object(service, "_sync_bot_to_bcn") as mock_sync:
            service.update_bot(
                bot_id="test-bot-id",
                user_id="85020",
                ext={"avatar_url": ""},
                sync_to_bcn=False,
            )

        mock_sync.assert_not_called()

    def test_local_write_failure_propagates_without_sync(self, mock_repository):
        """本地写失败向上传播（不得吞成成功），且不触发任何出站同步。"""
        mock_repository.update_by_owner.side_effect = RuntimeError("db down")
        service = _make_bot_service(repository=mock_repository)

        with patch.object(service, "_sync_bot_to_bcn") as mock_sync:
            with pytest.raises(BotServiceError, match="Failed to update bot"):
                service.update_bot(
                    bot_id="test-bot-id",
                    user_id="85020",
                    ext={"avatar_url": "https://example.com/saved.png"},
                )

        mock_sync.assert_not_called()

    def test_missing_row_after_update_propagates_without_sync(self, mock_repository):
        """行更新后缺失（BotNotFoundError 语句）同样先失败、不出站。"""
        mock_repository.update_by_owner.return_value = None
        service = _make_bot_service(repository=mock_repository)

        with patch.object(service, "_sync_bot_to_bcn") as mock_sync:
            with pytest.raises(BotNotFoundError):
                service.update_bot(
                    bot_id="test-bot-id",
                    user_id="85020",
                    ext={"avatar_url": "https://example.com/saved.png"},
                )

        mock_sync.assert_not_called()


class TestSyncBotToBcnAvatarProjection:
    """`_sync_bot_to_bcn` 头像投影与非成功日志。"""

    def _record(self, **ext) -> dict:
        return {
            "bot_id": "test-bot",
            "bot_name": "Existing Name",
            "bot_desc": "Existing Description",
            "owner_id": "85020",
            "ext": ext,
        }

    def _service(self, record: dict, bcn_result: dict | None = None) -> tuple:
        bcn = Mock()
        if bcn_result is not None:
            bcn.onboard_bot.return_value = bcn_result
        service = _make_bot_service(repository=_resolver_repo(record), bcn_service=bcn)
        return service, bcn

    def test_sync_projects_avatar_from_saved_record(self):
        """投影来自已保存记录（resolver 重新读取），随 onboard 透传。"""
        service, bcn = self._service(
            self._record(avatar_url="https://example.com/saved.png"),
            bcn_result={"bot_uuid": "u1", "onboarded": True},
        )

        service._sync_bot_to_bcn(bot_id="test-bot", owner_id="85020")

        bcn.onboard_bot.assert_called_once_with(
            bot_id="test-bot:85020",
            name="Existing Name",
            summary="Existing Description",
            avatar_url="https://example.com/saved.png",
        )

    def test_sync_sends_explicit_clear_for_saved_empty_avatar(self):
        """记录明确无头像时投影为 ``""``（显式清空），不是省略字段。"""
        service, bcn = self._service(
            self._record(avatar_url=""),
            bcn_result={"bot_uuid": "u1", "onboarded": True},
        )

        service._sync_bot_to_bcn(bot_id="test-bot", owner_id="85020")

        assert bcn.onboard_bot.call_args.kwargs["avatar_url"] == ""

    def test_update_flow_syncs_saved_merged_avatar(self, caplog):
        """端到端：只改头像的 update → 真实 sync 链 → onboard 收到落库后的头像。"""
        saved_record = {
            "bot_id": "test-bot",
            "bot_name": "Existing Name",
            "bot_desc": "Existing Description",
            "owner_id": "85020",
            "status": "ACTIVE",
            "binding_id": None,
            "ext": {"avatar_url": "https://example.com/saved.png"},
        }
        repo = _resolver_repo(saved_record)
        repo.get_by_bot_name.return_value = None
        bcn = Mock()
        bcn.onboard_bot.return_value = {"bot_uuid": "u1", "onboarded": True}
        service = _make_bot_service(repository=repo, bcn_service=bcn)

        caplog.set_level("INFO")
        result = service.update_bot(
            bot_id="test-bot",
            user_id="85020",
            ext={"avatar_url": "https://example.com/saved.png"},
        )

        assert result["bot_id"] == "test-bot"
        bcn.onboard_bot.assert_called_once_with(
            bot_id="test-bot:85020",
            name="Existing Name",
            summary="Existing Description",
            avatar_url="https://example.com/saved.png",
        )
        assert "Synced to BCN" in caplog.text

    def test_sync_omits_avatar_when_projection_unreadable(self, caplog):
        """读取失败不发送空头像：省略字段 + warning，名称/简介仍照常同步。"""
        service, bcn = self._service(
            self._record_only_corrupt_ext(),
            bcn_result={"bot_uuid": "u1", "onboarded": True},
        )

        caplog.set_level("INFO")
        service._sync_bot_to_bcn(
            bot_id="test-bot",
            owner_id="85020",
            bot_name="New Name",
            bot_desc="New Description",
        )

        bcn.onboard_bot.assert_called_once_with(
            bot_id="test-bot:85020",
            name="New Name",
            summary="New Description",
        )
        assert "avatar_url" not in bcn.onboard_bot.call_args.kwargs
        assert "avatar projection unreadable" in caplog.text

    @staticmethod
    def _record_only_corrupt_ext() -> dict:
        return {
            "bot_id": "test-bot",
            "bot_name": "Existing Name",
            "bot_desc": "Existing Description",
            "owner_id": "85020",
            "ext": "{",
        }

    def test_sync_logs_non_success_when_result_skipped(self, caplog):
        """结果 skipped 时必须记非成功日志，不得宣称 Synced。"""
        service, bcn = self._service(
            self._record(avatar_url="https://example.com/saved.png"),
            bcn_result={"skipped": True},
        )

        caplog.set_level("INFO")
        service._sync_bot_to_bcn(bot_id="test-bot", owner_id="85020")

        bcn.onboard_bot.assert_called_once()
        assert "BCN sync skipped" in caplog.text
        assert "Synced to BCN" not in caplog.text

    def test_sync_logs_non_success_when_onboarded_false(self, caplog):
        """结果 onboarded=false 时必须记非成功日志，不得宣称 Synced。"""
        service, bcn = self._service(self._record(), bcn_result={"bot_uuid": "u1", "onboarded": False})

        caplog.set_level("INFO")
        service._sync_bot_to_bcn(bot_id="test-bot", owner_id="85020")

        bcn.onboard_bot.assert_called_once()
        assert "did not confirm onboarding" in caplog.text
        assert "Synced to BCN" not in caplog.text

    def test_sync_logs_success_when_onboarded(self, caplog):
        service, bcn = self._service(
            self._record(avatar_url="https://example.com/saved.png"),
            bcn_result={"bot_uuid": "u1", "onboarded": True},
        )

        caplog.set_level("INFO")
        service._sync_bot_to_bcn(bot_id="test-bot", owner_id="85020")

        assert "Synced to BCN: bot_id=test-bot:85020" in caplog.text

    def test_downstream_failure_does_not_fail_the_edit(self, caplog):
        """下游同步失败保持 best-effort：编辑照常返回，出站失败记 warning。"""
        saved_record = {
            "bot_id": "test-bot",
            "bot_name": "Existing Name",
            "bot_desc": "Existing Description",
            "owner_id": "85020",
            "status": "ACTIVE",
            "binding_id": None,
            "ext": {"avatar_url": "https://example.com/saved.png"},
        }
        repo = _resolver_repo(saved_record)
        repo.get_by_bot_name.return_value = None
        bcn = Mock()
        bcn.onboard_bot.side_effect = BcnServiceError("BCN connection failed")
        service = _make_bot_service(repository=repo, bcn_service=bcn)

        caplog.set_level("INFO")
        result = service.update_bot(
            bot_id="test-bot",
            user_id="85020",
            ext={"avatar_url": "https://example.com/saved.png"},
        )

        assert result["bot_id"] == "test-bot"
        bcn.onboard_bot.assert_called_once()
        assert "Failed to sync to BCN" in caplog.text


class TestRegisterHelperAvatarBoundary:
    """注册 helper 的 ``avatar_url`` 边界参数三态。"""

    def _service(self) -> tuple:
        bcn = Mock()
        bcn.register_provider_bot.return_value = {"bot_uuid": "u1"}
        service = _make_bot_service(bcn_service=bcn)
        service.update_bot_ext = Mock()
        return service, bcn

    def test_register_helper_passes_avatar_value_through(self):
        service, bcn = self._service()

        service._register_bot_to_bcn_as_provider(
            bot_id="bot",
            user_id="u1",
            owner_workno="u1",
            bot_name="Name",
            bot_summary="",
            respect_drm=False,
            avatar_url="https://example.com/a.png",
        )

        assert (
            bcn.register_provider_bot.call_args.kwargs["avatar_url"]
            == "https://example.com/a.png"
        )

    def test_register_helper_sends_explicit_clear(self):
        """``""`` 是显式清空，必须出站，不能被当成省略。"""
        service, bcn = self._service()

        service._register_bot_to_bcn_as_provider(
            bot_id="bot",
            user_id="u1",
            owner_workno="u1",
            bot_name="Name",
            bot_summary="",
            respect_drm=False,
            avatar_url="",
        )

        assert bcn.register_provider_bot.call_args.kwargs["avatar_url"] == ""
        service.update_bot_ext.assert_called_once_with(
            bot_id="bot", user_id="u1", ext_update={"bcn_registered": True}
        )

    def test_register_helper_omits_avatar_when_none(self):
        """省略（``None``）= 不发送该字段，BCN 保留旧值。"""
        service, bcn = self._service()

        service._register_bot_to_bcn_as_provider(
            bot_id="bot",
            user_id="u1",
            owner_workno="u1",
            bot_name="Name",
            bot_summary="",
            respect_drm=False,
        )

        assert "avatar_url" not in bcn.register_provider_bot.call_args.kwargs

    def test_register_helper_skipped_result_still_skips_marker_write(self):
        """skipped 路径行为不变：不写 bcn_registered 标记。"""
        service, bcn = self._service()
        bcn.register_provider_bot.return_value = {
            "bot_uuid": "",
            "provider_id": "",
            "provider_bot_ref": "bot:u1",
            "bot_runtime_token": "",
            "skipped": True,
        }

        service._register_bot_to_bcn_as_provider(
            bot_id="bot",
            user_id="u1",
            owner_workno="u1",
            bot_name="Name",
            bot_summary="",
            respect_drm=False,
            avatar_url="",
        )

        assert bcn.register_provider_bot.call_args.kwargs["avatar_url"] == ""
        service.update_bot_ext.assert_not_called()


class TestStartBotRegistersSavedAvatar:
    """start_bot 真实调用链：注册按既有条件透传已保存记录的头像投影。"""

    def _startable_bot(self, ext) -> dict:
        return {
            "bot_id": "bot001",
            "owner_id": "user001",
            "status": "ACTIVE",
            "binding_id": 42,
            "entity_id": "staff_user001",
            "entity_type": "staff",
            "active_engine": "openclaw",
            "bot_type": "personal",
            "bot_name": "TestBot",
            "bot_desc": "summary",
            "ext": ext,
        }

    def test_start_bot_passes_register_avatar_projection(self):
        bot = self._startable_bot({"avatar_url": "https://example.com/a.png"})
        repo = Mock()
        repo.get_by_id_and_owner.side_effect = [bot, bot]
        bcn = Mock()
        bcn.register_provider_bot.return_value = {"bot_uuid": "u1"}
        service = _make_bot_service(repository=repo, bcn_service=bcn)

        with patch.object(service, "_allocate_device_async"), \
             patch.object(service, "update_bot_ext"):
            service.start_bot(bot_id="bot001", user_id="user001")

        bcn.register_provider_bot.assert_called_once_with(
            teamclaw_bot_uuid="bot001",
            owner_workno="user001",
            name="TestBot",
            summary="summary",
            connection_mode="plugin",
            avatar_url="https://example.com/a.png",
        )

    def test_start_bot_omits_avatar_when_projection_unreadable(self):
        """记录读不出来时注册照常发起（best-effort 不中断），头像字段省略。"""
        bot = self._startable_bot("{")
        repo = Mock()
        repo.get_by_id_and_owner.side_effect = [bot, bot]
        bcn = Mock()
        bcn.register_provider_bot.return_value = {"bot_uuid": "u1"}
        service = _make_bot_service(repository=repo, bcn_service=bcn)

        with patch.object(service, "_allocate_device_async"), \
             patch.object(service, "update_bot_ext"):
            service.start_bot(bot_id="bot001", user_id="user001")

        bcn.register_provider_bot.assert_called_once()
        assert "avatar_url" not in bcn.register_provider_bot.call_args.kwargs
