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


class TestApplyRpcSelection:
    """发证 RPC 只看 ``use_first_passport``,与租户无关。

    This class used to be ``TestApplyRpcTenantBranch`` and asserted the
    opposite for non-default tenants: they took applyFirst unconditionally
    (#556). The branch is gone — ``use_first_passport`` is now the only input —
    so the parametrization keeps both tenants and asserts the *same* answer for
    each, which is the property that would break if the branch came back.
    """

    @pytest.mark.parametrize("tenant", ["teamclaw", "tenantB"])
    @pytest.mark.parametrize(
        "use_first_passport,expect_first_rpc",
        [
            (True, True),    # 首个 bot → applyFirst(跳过审批)
            (False, False),  # 非首 → applyAgent(走审批)
        ],
    )
    def test_rpc_selection(self, tenant, use_first_passport, expect_first_rpc):
        from agentclaw.community.core.bot_management import create_flow
        from agentclaw.community.utils.avernet_tenant import (
            DEFAULT_AVERNET_TENANT,
            avernet_tenant_scope,
        )

        # 一行是默认租户、一行不是 —— 否则「与租户无关」无从断言。
        assert DEFAULT_AVERNET_TENANT == "teamclaw"

        plugin = MagicMock()
        plugin.apply_first_agent_passport.return_value = {"token": "t"}
        plugin.apply_agent_passport.return_value = {"token": "t"}

        # A real scope rather than a patched reader: the point is that the
        # tenant genuinely differs between the two rows and the answer does not.
        with avernet_tenant_scope(tenant):
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
