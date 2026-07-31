"""is_first_bot 现按 owner 当前 bot 数==0 派生,不再依赖 bot_id=='default'。"""
from __future__ import annotations

from unittest.mock import MagicMock

from agentclaw.community.core.bot_management.services.bot_service import BotService


def _svc_with_count(count: int) -> BotService:
    svc = BotService.__new__(BotService)
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
