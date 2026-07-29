from unittest.mock import MagicMock

from agentclaw.community.core.notify.local_bot_lister import LocalNotifyBotLister
from agentclaw.community.core.notify.protocol import NotifyTarget


def test_list_bot_mappings_uses_active_personal_bots_from_repository():
    bot_repo = MagicMock()
    bot_repo.list_active_bots_by_entity.return_value = [
        {
            "bot_id": "bot1", "bot_name": "Alpha", "binding_id": 101,
            "active_engine": "aicoding",
        },
        {
            "bot_id": "bot2", "bot_name": "Beta", "binding_id": None,
            "active_engine": "claude_code",
        },
        {"bot_id": "bot3", "binding_id": 103, "active_engine": "claude_code"},
    ]
    lister = LocalNotifyBotLister(bot_repository=bot_repo)

    assert lister.list_bot_mappings("u001") == [
        NotifyTarget("bot1", "Alpha", "u001", "101"),
        NotifyTarget("bot3", "bot3", "u001", "103"),
    ]
    bot_repo.list_active_bots_by_entity.assert_called_once_with(
        entity_id="u001",
        entity_type="staff",
        bot_type="personal",
    )


def test_list_bot_mappings_skips_empty_bot_id():
    """Entries with falsy bot_id are filtered out."""
    bot_repo = MagicMock()
    bot_repo.list_active_bots_by_entity.return_value = [
        {
            "bot_id": "", "bot_name": "Ghost", "binding_id": 200,
            "active_engine": "aicoding",
        },
        {
            "bot_id": "real", "bot_name": "OK", "binding_id": 201,
            "active_engine": "claude_code",
        },
    ]
    lister = LocalNotifyBotLister(bot_repository=bot_repo)
    assert lister.list_bot_mappings("u002") == [NotifyTarget("real", "OK", "u002", "201")]


def test_list_bot_mappings_only_includes_notify_supported_engines():
    bot_repo = MagicMock()
    bot_repo.list_active_bots_by_entity.return_value = [
        {
            "bot_id": "aicoding", "bot_name": "AI Coding", "binding_id": 101,
            "active_engine": "aicoding",
        },
        {
            "bot_id": "claude", "bot_name": "Claude", "binding_id": 102,
            "active_engine": "claude_code",
        },
        {
            "bot_id": "openclaw", "bot_name": "OpenClaw", "binding_id": 103,
            "active_engine": "openclaw",
        },
        {
            "bot_id": "missing", "bot_name": "Missing", "binding_id": 104,
        },
    ]
    lister = LocalNotifyBotLister(bot_repository=bot_repo)

    assert lister.list_bot_mappings("u003") == [
        NotifyTarget("aicoding", "AI Coding", "u003", "101"),
        NotifyTarget("claude", "Claude", "u003", "102"),
    ]
