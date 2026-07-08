from unittest.mock import MagicMock

from agentclaw.community.core.notify.local_bot_lister import LocalNotifyBotLister


def test_list_bot_mappings_uses_active_personal_bots_from_repository():
    bot_repo = MagicMock()
    bot_repo.list_active_bots_by_entity.return_value = [
        {"bot_id": "bot1", "bot_name": "Alpha", "binding_id": 101},
        {"bot_id": "bot2", "bot_name": "Beta", "binding_id": None},
        {"bot_id": "bot3", "binding_id": 103},
    ]
    lister = LocalNotifyBotLister(bot_repository=bot_repo)

    assert lister.list_bot_mappings("u001") == [
        ("bot1", "Alpha", "101"),
        ("bot3", "bot3", "103"),
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
        {"bot_id": "", "bot_name": "Ghost", "binding_id": 200},
        {"bot_id": "real", "bot_name": "OK", "binding_id": 201},
    ]
    lister = LocalNotifyBotLister(bot_repository=bot_repo)
    assert lister.list_bot_mappings("u002") == [("real", "OK", "201")]
