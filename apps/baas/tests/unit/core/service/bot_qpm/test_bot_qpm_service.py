"""Unit tests for DefaultBotQpmManageService."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from secbaas.community.core.repository.bot_qpm import BotQpmRecord
from secbaas.community.core.service.bot_qpm._bot_qpm_service import (
    DefaultBotQpmManageService,
)


@pytest.fixture
def mock_repo():
    return MagicMock()


@pytest.fixture
def service(mock_repo):
    return DefaultBotQpmManageService(mock_repo)


def _make_record(**kwargs):
    defaults = dict(
        id=1,
        bot_id="bot-001",
        qpm=100,
        env="prod",
        gmt_create=datetime(2025, 1, 1),
        gmt_modified=datetime(2025, 1, 2),
    )
    defaults.update(kwargs)
    return BotQpmRecord(**defaults)


def test_list_configs(service, mock_repo):
    mock_repo.list_all.return_value = [
        _make_record(),
        _make_record(id=2, bot_id="bot-002"),
    ]
    result = service.list_configs()
    assert result.total == 2
    assert len(result.items) == 2
    assert result.items[0].bot_id == "bot-001"
    assert result.items[0].qpm == 100


def test_list_configs_empty(service, mock_repo):
    mock_repo.list_all.return_value = []
    result = service.list_configs()
    assert result.total == 0
    assert len(result.items) == 0


def test_get_config_found(service, mock_repo):
    mock_repo.get_by_bot_id.return_value = _make_record()
    result = service.get_config("bot-001")
    assert result is not None
    assert result.bot_id == "bot-001"
    assert result.qpm == 100


def test_get_config_not_found(service, mock_repo):
    mock_repo.get_by_bot_id.return_value = None
    result = service.get_config("bot-001")
    assert result is None


def test_upsert_config(service, mock_repo):
    mock_repo.get_by_bot_id.return_value = _make_record(qpm=200)
    result = service.upsert_config(bot_id="bot-001", qpm=200)
    assert result.bot_id == "bot-001"
    assert result.qpm == 200
    mock_repo.upsert.assert_called_once_with(bot_id="bot-001", qpm=200)


def test_update_config_found(service, mock_repo):
    mock_repo.get_by_bot_id.return_value = _make_record(qpm=300)
    result = service.update_config(bot_id="bot-001", qpm=300)
    assert result is not None
    assert result.qpm == 300
    mock_repo.upsert.assert_called_once_with(bot_id="bot-001", qpm=300)


def test_update_config_not_found(service, mock_repo):
    mock_repo.get_by_bot_id.return_value = None
    result = service.update_config(bot_id="bot-001", qpm=300)
    assert result is None
    mock_repo.upsert.assert_not_called()


def test_delete_config_success(service, mock_repo):
    mock_repo.delete.return_value = True
    result = service.delete_config("bot-001")
    assert result is True


def test_delete_config_failure(service, mock_repo):
    mock_repo.delete.return_value = False
    result = service.delete_config("bot-001")
    assert result is False
