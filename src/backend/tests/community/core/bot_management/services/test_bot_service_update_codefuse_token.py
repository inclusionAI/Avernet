"""Tests for update_bot codefuse-token dispatch when template_config.token changes.

PUT /api/bots/{bot_id} 在 applicationCoding bot 的 ``template_config.token`` 变化时，
应把解密后的明文 auth_code 异步写入运行中容器的 codefuse.json，使前端无需再单独
调 ``PUT /aicoding/bots/{bot_id}/codefuse/auth``。
"""

import pytest
from unittest.mock import MagicMock, Mock, patch

from agentclaw.community.core.bot_management.services.bot_service import BotService


def _fake_cmd(auth_code: str) -> str:
    return f"FAKE_CMD({auth_code})"


def _make_bot_service(repository=None, template_service=None) -> BotService:
    return BotService(
        drm_reader=MagicMock(),
        repository=repository or Mock(),
        allocation_config=MagicMock(),
        device_binding_repo=MagicMock(),
        skill_set_factory=MagicMock(),
        cleanup_service=MagicMock(),
        bcn_service=MagicMock(),
        bot_publish_repo=MagicMock(),
        passport_plugin=MagicMock(),
        oss_record_repo=MagicMock(),
        bot_publish_service_provider=lambda: MagicMock(),
        device_service_provider=lambda: MagicMock(),
        path_factory=MagicMock(),
        template_service=template_service or MagicMock(),
        workspace_hosting_service=MagicMock(),
        collaborator_repo=MagicMock(),
        restart_lock_repo=MagicMock(),
        teclaw_provision_service_provider=lambda: MagicMock(is_teclaw=MagicMock(return_value=False)),
        device_status_client=MagicMock(),
        cron_auto_setup_service_provider=lambda: MagicMock(),
    )


def _bot_record(**overrides):
    base = {
        "id": 1,
        "bot_id": "bot-1",
        "bot_name": "TestBot",
        "bot_desc": "desc",
        "owner_id": "user1",
        "status": "ACTIVE",
        "binding_id": 100,
        "ext": {},
        "template_type": "applicationCoding",
        "active_engine": "claude_code",
    }
    base.update(overrides)
    return base


class _FakeThread:
    """替换 threading.Thread：构造时同步执行 target，便于断言副作用。"""

    instances = []

    def __init__(self, target=None, args=(), kwargs=None, daemon=False, name=None):
        self.target = target
        self.kwargs = kwargs or {}
        _FakeThread.instances.append(self)
        if target is not None:
            target(**self.kwargs)

    def start(self):
        pass


class TestUpdateBotCodefuseTokenDispatch:
    """update_bot 在 token 变化时下发新 codefuse token 到运行中容器。"""

    @pytest.fixture(autouse=True)
    def _reset_fake_thread(self):
        _FakeThread.instances.clear()
        yield
        _FakeThread.instances.clear()

    @pytest.fixture
    def repo(self):
        repo = Mock()
        repo.get_by_id_and_owner.return_value = _bot_record()
        repo.update_by_owner.return_value = _bot_record()
        repo.get_by_bot_name.return_value = None
        return repo

    @pytest.fixture
    def template_svc(self):
        svc = MagicMock()
        svc.exists_template.return_value = True
        svc.get_template.return_value = {"ext": {}}
        # 新 token 解密后的明文 auth_code（落库后 get_decrypted_codefuse_token 返回）
        svc.get_decrypted_codefuse_token.return_value = "new-plaintext-auth-code"
        return svc

    def _wire_get_template_config(self, svc, old_db_token, new_db_token):
        """让 get_template_config 体现两次调用：落库前返 old 密文，落库后回读返 new 密文。

        update_bot 落库前抓 old（bot_service.py:2050），_maybe_refresh 落库后回读
        新密文做同口径比较；两次调用顺序固定，用 side_effect 列表表达。
        """
        svc.get_template_config.side_effect = [
            {"token": old_db_token},
            {"token": new_db_token},
        ]

    # ── 触发 ────────────────────────────────────────────────────

    def test_dispatches_when_token_changes(self, repo, template_svc):
        """token 变化（DB 密文不同）时，以解密后明文调用 _refresh_codefuse_token_on_device。

        真实链路：前端入参是明文 token，落库后被加密成新密文；old 是落库前 DB 里的
        旧密文。两者同为 DB 密文、不同 → 触发。
        """
        self._wire_get_template_config(
            template_svc,
            old_db_token="enc:v1:OLD_CIPHERTEXT",
            new_db_token="enc:v1:NEW_CIPHERTEXT",
        )
        service = _make_bot_service(repository=repo, template_service=template_svc)
        new_config = {"token": "new-plaintext-auth-code"}  # 前端明文入参

        with patch(
            "agentclaw.community.core.bot_management.services.bot_service.threading.Thread",
            _FakeThread,
        ):
            with patch.object(
                service, "_refresh_codefuse_token_on_device"
            ) as mock_refresh:
                service.update_bot(
                    bot_id="bot-1",
                    user_id="user1",
                    template_config=new_config,
                    cookie="c",
                )
                mock_refresh.assert_called_once_with(
                    bot_id="bot-1",
                    user_id="user1",
                    plaintext_token="new-plaintext-auth-code",
                )

    def test_no_dispatch_when_token_unchanged(self, repo, template_svc):
        """用户没改 token（前端原样回传）时不下发。

        真实链路：old DB 密文 == 落库后回读的新 DB 密文（同一 token 加密产物相同）
        → 跳过。注意入参是明文，绝不能拿明文与密文直接比。
        """
        self._wire_get_template_config(
            template_svc,
            old_db_token="enc:v1:SAME_CIPHERTEXT",
            new_db_token="enc:v1:SAME_CIPHERTEXT",
        )
        service = _make_bot_service(repository=repo, template_service=template_svc)
        new_config = {"token": "same-plaintext-auth-code"}  # 前端明文，与旧值相同

        with patch(
            "agentclaw.community.core.bot_management.services.bot_service.threading.Thread",
            _FakeThread,
        ):
            with patch.object(
                service, "_refresh_codefuse_token_on_device"
            ) as mock_refresh:
                service.update_bot(
                    bot_id="bot-1",
                    user_id="user1",
                    template_config=new_config,
                    cookie="c",
                )
                mock_refresh.assert_not_called()

    def test_no_dispatch_when_template_config_has_no_token(self, repo, template_svc):
        """template_config 未携带 token 字段时不触发（如仅改 yuque_kb_repos）。"""
        template_svc.get_template_config.return_value = {"yuque_kb_repos": []}
        service = _make_bot_service(repository=repo, template_service=template_svc)
        new_config = {"yuque_kb_repos": [{"url": "https://yuque/a"}]}

        with patch(
            "agentclaw.community.core.bot_management.services.bot_service.threading.Thread",
            _FakeThread,
        ):
            with patch.object(
                service, "_refresh_codefuse_token_on_device"
            ) as mock_refresh:
                service.update_bot(
                    bot_id="bot-1",
                    user_id="user1",
                    template_config=new_config,
                    cookie="c",
                )
                mock_refresh.assert_not_called()

    # ── 条件过滤 ─────────────────────────────────────────────────

    def test_no_dispatch_when_non_coding_template_type(self, repo, template_svc):
        repo.get_by_id_and_owner.return_value = _bot_record(template_type="sometype")
        repo.update_by_owner.return_value = _bot_record(template_type="sometype")
        service = _make_bot_service(repository=repo, template_service=template_svc)

        with patch(
            "agentclaw.community.core.bot_management.services.bot_service.threading.Thread",
            _FakeThread,
        ):
            with patch.object(
                service, "_refresh_codefuse_token_on_device"
            ) as mock_refresh:
                service.update_bot(
                    bot_id="bot-1",
                    user_id="user1",
                    template_config={"token": "enc:v1:NEW"},
                    cookie="c",
                )
                mock_refresh.assert_not_called()

    def test_dispatches_when_personal_coding(self, repo, template_svc):
        """personalCoding 同 applicationCoding：token 变化时也异步下发到容器。"""
        repo.get_by_id_and_owner.return_value = _bot_record(template_type="personalCoding")
        repo.update_by_owner.return_value = _bot_record(template_type="personalCoding")
        self._wire_get_template_config(
            template_svc,
            old_db_token="enc:v1:OLD_CIPHERTEXT",
            new_db_token="enc:v1:NEW_CIPHERTEXT",
        )
        service = _make_bot_service(repository=repo, template_service=template_svc)
        new_config = {"token": "new-plaintext-auth-code"}

        with patch(
            "agentclaw.community.core.bot_management.services.bot_service.threading.Thread",
            _FakeThread,
        ):
            with patch.object(
                service, "_refresh_codefuse_token_on_device"
            ) as mock_refresh:
                service.update_bot(
                    bot_id="bot-1",
                    user_id="user1",
                    template_config=new_config,
                    cookie="c",
                )
                mock_refresh.assert_called_once_with(
                    bot_id="bot-1",
                    user_id="user1",
                    plaintext_token="new-plaintext-auth-code",
                )

    def test_no_dispatch_when_no_template_config(self, repo, template_svc):
        service = _make_bot_service(repository=repo, template_service=template_svc)

        with patch(
            "agentclaw.community.core.bot_management.services.bot_service.threading.Thread",
            _FakeThread,
        ):
            with patch.object(
                service, "_refresh_codefuse_token_on_device"
            ) as mock_refresh:
                service.update_bot(
                    bot_id="bot-1",
                    user_id="user1",
                    bot_name="New Name",
                    cookie="c",
                )
                mock_refresh.assert_not_called()

    # ── 容错 ─────────────────────────────────────────────────────

    def test_dispatch_failure_does_not_break_update(self, repo, template_svc):
        """异步下发失败不应阻断 update_bot 主流程。"""
        service = _make_bot_service(repository=repo, template_service=template_svc)

        with patch(
            "agentclaw.community.core.bot_management.services.bot_service.threading.Thread",
            _FakeThread,
        ):
            with patch.object(
                service,
                "_refresh_codefuse_token_on_device",
                side_effect=Exception("exec failed"),
            ):
                result = service.update_bot(
                    bot_id="bot-1",
                    user_id="user1",
                    template_config={"token": "enc:v1:NEW"},
                    cookie="c",
                )
                assert result is not None


class TestRefreshCodefuseTokenOnDevice:
    """_refresh_codefuse_token_on_device 按 provider 路由写入容器。"""

    @pytest.fixture
    def service(self):
        svc = _make_bot_service()
        svc._baas_service_provider = lambda: MagicMock(name="baas_service")
        return svc

    def test_baas_provider_writes_via_write_codefuse_token_baas(self, service):
        bot = _bot_record(binding_id=100)
        service._repository.get_by_id_and_owner.return_value = bot
        binding = MagicMock()
        binding.device_provider = "baas"
        binding.device_props = {"bot_uuid": "uuid-xyz"}
        service._device_binding_repo.get_by_id.return_value = binding
        device_service = MagicMock()
        service._device_service_provider = lambda: device_service

        with patch(
            "agentclaw.community.core.devices.services.baas_codefuse_writer.write_codefuse_token_baas"
        ) as mock_write:
            service._refresh_codefuse_token_on_device(
                bot_id="bot-1", user_id="user1", plaintext_token="plain-tok"
            )
            mock_write.assert_called_once()
            device_service.exec_shell.assert_not_called()

    def test_arca_provider_writes_via_exec_shell(self, service):
        bot = _bot_record(binding_id=100)
        service._repository.get_by_id_and_owner.return_value = bot
        binding = MagicMock()
        binding.device_provider = "arca"
        binding.device_id = "dev-001"
        service._device_binding_repo.get_by_id.return_value = binding
        device_service = MagicMock()
        service._device_service_provider = lambda: device_service

        with patch(
            "agentclaw.community.core.bot_management.codefuse_token.build_codefuse_write_cmd_from_auth_code",
            side_effect=_fake_cmd,
        ):
            with patch(
                "agentclaw.community.core.devices.services.baas_codefuse_writer.write_codefuse_token_baas"
            ) as mock_write:
                service._refresh_codefuse_token_on_device(
                    bot_id="bot-1", user_id="user1", plaintext_token="plain-tok"
                )
                mock_write.assert_not_called()
                device_service.exec_shell.assert_called_once_with(
                    device_id="dev-001", shell_cmd="FAKE_CMD(plain-tok)"
                )

    def test_no_binding_skips_silently(self, service):
        service._repository.get_by_id_and_owner.return_value = _bot_record(binding_id=None)
        device_service = MagicMock()
        service._device_service_provider = lambda: device_service

        with patch(
            "agentclaw.community.core.devices.services.baas_codefuse_writer.write_codefuse_token_baas"
        ) as mock_write:
            service._refresh_codefuse_token_on_device(
                bot_id="bot-1", user_id="user1", plaintext_token="plain-tok"
            )
            mock_write.assert_not_called()
            device_service.exec_shell.assert_not_called()

    def test_bot_not_found_skips_silently(self, service):
        """bot 查不到时跳过，不抛出。"""
        service._repository.get_by_id_and_owner.return_value = None
        device_service = MagicMock()
        service._device_service_provider = lambda: device_service

        with patch(
            "agentclaw.community.core.devices.services.baas_codefuse_writer.write_codefuse_token_baas"
        ) as mock_write:
            service._refresh_codefuse_token_on_device(
                bot_id="bot-1", user_id="user1", plaintext_token="plain-tok"
            )
            mock_write.assert_not_called()
            device_service.exec_shell.assert_not_called()

    def test_binding_record_not_found_skips_silently(self, service):
        """binding 查不到时跳过。"""
        service._repository.get_by_id_and_owner.return_value = _bot_record(binding_id=100)
        service._device_binding_repo.get_by_id.return_value = None
        device_service = MagicMock()
        service._device_service_provider = lambda: device_service

        with patch(
            "agentclaw.community.core.devices.services.baas_codefuse_writer.write_codefuse_token_baas"
        ) as mock_write:
            service._refresh_codefuse_token_on_device(
                bot_id="bot-1", user_id="user1", plaintext_token="plain-tok"
            )
            mock_write.assert_not_called()
            device_service.exec_shell.assert_not_called()

    def test_baas_missing_bot_uuid_skips_silently(self, service):
        """baas provider 但 device_props 无 bot_uuid 时跳过。"""
        service._repository.get_by_id_and_owner.return_value = _bot_record(binding_id=100)
        binding = MagicMock()
        binding.device_provider = "baas"
        binding.device_props = {}  # 无 bot_uuid
        service._device_binding_repo.get_by_id.return_value = binding
        device_service = MagicMock()
        service._device_service_provider = lambda: device_service

        with patch(
            "agentclaw.community.core.devices.services.baas_codefuse_writer.write_codefuse_token_baas"
        ) as mock_write:
            service._refresh_codefuse_token_on_device(
                bot_id="bot-1", user_id="user1", plaintext_token="plain-tok"
            )
            mock_write.assert_not_called()
            device_service.exec_shell.assert_not_called()

    def test_non_baas_missing_device_id_skips_silently(self, service):
        """非 baas provider 但无 device_id 时跳过。"""
        service._repository.get_by_id_and_owner.return_value = _bot_record(binding_id=100)
        binding = MagicMock()
        binding.device_provider = "arca"
        binding.device_id = None  # 无 device_id
        service._device_binding_repo.get_by_id.return_value = binding
        device_service = MagicMock()
        service._device_service_provider = lambda: device_service

        with patch(
            "agentclaw.community.core.devices.services.baas_codefuse_writer.write_codefuse_token_baas"
        ) as mock_write:
            with patch(
                "agentclaw.community.core.bot_management.codefuse_token.build_codefuse_write_cmd_from_auth_code",
                side_effect=_fake_cmd,
            ):
                service._refresh_codefuse_token_on_device(
                    bot_id="bot-1", user_id="user1", plaintext_token="plain-tok"
                )
                mock_write.assert_not_called()
                device_service.exec_shell.assert_not_called()

    def test_refresh_swallows_unexpected_exception(self, service):
        """_refresh 顶层异常被吞，不向调用方抛出。"""
        service._repository.get_by_id_and_owner.side_effect = RuntimeError("db down")

        with patch(
            "agentclaw.community.core.devices.services.baas_codefuse_writer.write_codefuse_token_baas"
        ) as mock_write:
            # 不应抛出
            service._refresh_codefuse_token_on_device(
                bot_id="bot-1", user_id="user1", plaintext_token="plain-tok"
            )
            mock_write.assert_not_called()


class TestMaybeRefreshPrepareFailure:
    """_maybe_refresh_codefuse_token_async prepare 段容错。"""

    def test_prepare_exception_does_not_dispatch(self):
        """get_template_config 抛异常时进入 except，不起线程下发。"""
        repo = Mock()
        repo.get_by_id_and_owner.return_value = _bot_record()
        repo.update_by_owner.return_value = _bot_record()
        repo.get_by_bot_name.return_value = None

        template_svc = MagicMock()
        template_svc.exists_template.return_value = True
        template_svc.get_template.return_value = {"ext": {}}
        template_svc.get_template_config.side_effect = RuntimeError("db error")

        service = _make_bot_service(repository=repo, template_service=template_svc)

        with patch(
            "agentclaw.community.core.bot_management.services.bot_service.threading.Thread"
        ) as mock_thread:
            service.update_bot(
                bot_id="bot-1",
                user_id="user1",
                template_config={"token": "enc:v1:NEW"},
                cookie="c",
            )
            mock_thread.assert_not_called()

    def test_empty_string_token_does_not_dispatch(self):
        """入参 token 为空串/null/非字符串时直接 return，不下发。"""
        repo = Mock()
        repo.get_by_id_and_owner.return_value = _bot_record()
        repo.update_by_owner.return_value = _bot_record()
        repo.get_by_bot_name.return_value = None

        template_svc = MagicMock()
        template_svc.exists_template.return_value = True
        template_svc.get_template.return_value = {"ext": {}}

        service = _make_bot_service(repository=repo, template_service=template_svc)

        for empty_token in ("", None, 123):
            template_svc.reset_mock()
            with patch(
                "agentclaw.community.core.bot_management.services.bot_service.threading.Thread"
            ) as mock_thread:
                service.update_bot(
                    bot_id="bot-1",
                    user_id="user1",
                    template_config={"token": empty_token},
                    cookie="c",
                )
                mock_thread.assert_not_called()

    def test_decrypted_plaintext_empty_does_not_dispatch(self):
        """解密后明文为空（get_decrypted_codefuse_token 返 None）时不下发。"""
        repo = Mock()
        repo.get_by_id_and_owner.return_value = _bot_record()
        repo.update_by_owner.return_value = _bot_record()
        repo.get_by_bot_name.return_value = None

        template_svc = MagicMock()
        template_svc.exists_template.return_value = True
        template_svc.get_template.return_value = {"ext": {}}
        # 落库前 old / 落库后 new 均有 token 密文（不同 → 通过去重）
        template_svc.get_template_config.side_effect = [
            {"token": "enc:v1:OLD"},
            {"token": "enc:v1:NEW"},
        ]
        # 但解密返回 None（如密文损坏 / 无密钥场景兜底）
        template_svc.get_decrypted_codefuse_token.return_value = None

        service = _make_bot_service(repository=repo, template_service=template_svc)

        with patch(
            "agentclaw.community.core.bot_management.services.bot_service.threading.Thread"
        ) as mock_thread:
            service.update_bot(
                bot_id="bot-1",
                user_id="user1",
                template_config={"token": "enc:v1:NEW"},
                cookie="c",
            )
            mock_thread.assert_not_called()
