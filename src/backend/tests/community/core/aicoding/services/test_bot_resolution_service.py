"""Unit tests for AICoding bot resolution helpers."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from agentclaw.community.core.aicoding.services.bot_resolution_service import (
    AicodingBotResolutionService,
)


def _make_service() -> AicodingBotResolutionService:
    return AicodingBotResolutionService(bot_repo=MagicMock(), collaborator_repo=MagicMock())


class TestResolveBotForDimaWorkspace:
    def test_prefers_exact_owner_lookup(self):
        svc = _make_service()
        bot = {"bot_id": "bot-001", "owner_id": "owner-492928", "id": 1}
        svc._bot_repo.get_by_id_and_owner.side_effect = [bot]

        resolved = svc.resolve_bot_for_dima_workspace(
            bot_id="bot-001",
            requested_owner_id="owner-492928",
            operator_id="member-382716",
            env="dev",
        )

        assert resolved == bot
        svc._bot_repo.get_by_id_and_owner.assert_called_once_with("bot-001", "owner-492928")
        svc._collaborator_repo.list_by_user.assert_not_called()

    def test_falls_back_to_collaborator_owner(self):
        svc = _make_service()
        bot = {"bot_id": "bot-001", "owner_id": "owner-492928", "id": 1}
        svc._bot_repo.get_by_id_and_owner.side_effect = [None, bot]
        svc._collaborator_repo.list_by_user.return_value = [
            SimpleNamespace(bot_id="bot-001", owner_id="owner-492928"),
        ]

        resolved = svc.resolve_bot_for_dima_workspace(
            bot_id="bot-001",
            requested_owner_id="member-382716",
            operator_id="member-382716",
            env="dev",
        )

        assert resolved == bot
        assert svc._bot_repo.get_by_id_and_owner.call_args_list[0].args == ("bot-001", "member-382716")
        svc._collaborator_repo.list_by_user.assert_called_once()
        assert svc._collaborator_repo.list_by_user.call_args.args == ("member-382716", "dev")
        assert svc._bot_repo.get_by_id_and_owner.call_args_list[1].args == ("bot-001", "owner-492928")
