"""is_first_bot 现按 owner 当前 bot 数==0 派生,不再依赖 bot_id=='default'。"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.services.bot_service import BotService


def _svc_with_count(count: int) -> BotService:
    svc = BotService.__new__(BotService)
    svc._bot_app_grant_provider = lambda: MagicMock()
    svc._repository = MagicMock()
    svc._repository.count_by_owner.return_value = count
    return svc


class TestIsFirstBot:
    def test_zero_bots_is_first(self):
        svc = _svc_with_count(0)
        assert svc.is_first_bot("user001") is True
        svc._repository.count_by_owner.assert_called_once_with("user001")

    def test_one_bot_not_first(self):
        assert _svc_with_count(1).is_first_bot("user001") is False

    def test_many_bots_not_first(self):
        assert _svc_with_count(5).is_first_bot("user001") is False


class TestIsFirstPersonalBot:
    def test_no_live_personal_bot_is_first_personal(self):
        svc = BotService.__new__(BotService)
        svc._bot_app_grant_provider = lambda: MagicMock()
        svc._repository = MagicMock()
        svc._repository.exists_by_owner_and_bot_type.return_value = False

        assert svc.is_first_personal_bot("user001") is True
        svc._repository.exists_by_owner_and_bot_type.assert_called_once_with(
            "user001", "personal"
        )

    def test_existing_live_personal_bot_is_not_first_personal(self):
        svc = BotService.__new__(BotService)
        svc._bot_app_grant_provider = lambda: MagicMock()
        svc._repository = MagicMock()
        svc._repository.exists_by_owner_and_bot_type.return_value = True

        assert svc.is_first_personal_bot("user001") is False


class TestApplyRpcTenantBranch:
    """发证 RPC 按租户分流:默认租户按首个个人 Bot 分,其他租户一律 applyFirst。"""

    @pytest.mark.parametrize(
        "tenant,use_first_passport,expect_first_rpc",
        [
            ("teamclaw", True, True),    # 默认租户首 bot → applyFirst
            ("teamclaw", False, False),  # 默认租户非首 → applyAgent
            ("tenantB", True, True),     # 其他租户 → 一律 applyFirst
            ("tenantB", False, True),    # 其他租户非首 → 仍 applyFirst
        ],
    )
    def test_rpc_selection(
        self, tenant, use_first_passport, expect_first_rpc, monkeypatch
    ):
        from agentclaw.community.core.bot_management import create_flow
        from agentclaw.community.utils.avernet_tenant import DEFAULT_AVERNET_TENANT

        # 确认 parametrize 行的默认租户与真实常量一致(否则断言无意义)。
        assert DEFAULT_AVERNET_TENANT == "teamclaw"

        monkeypatch.setattr(
            create_flow, "get_current_avernet_tenant", lambda: tenant
        )
        plugin = MagicMock()
        plugin.apply_first_agent_passport.return_value = {"token": "t"}
        plugin.apply_agent_passport.return_value = {"token": "t"}

        create_flow._apply_passport(
            plugin,
            bot_id="20260731_abcd1234",
            user_id="user001",
            bot_name=None,
            spec=MagicMock(),
            mcp_codes=[],
            cli_items=[],
            use_first_passport=use_first_passport,
        )

        if expect_first_rpc:
            plugin.apply_first_agent_passport.assert_called_once()
            plugin.apply_agent_passport.assert_not_called()
        else:
            plugin.apply_agent_passport.assert_called_once()
            plugin.apply_first_agent_passport.assert_not_called()
