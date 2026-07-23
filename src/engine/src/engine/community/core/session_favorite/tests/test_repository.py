from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import engine.community.core.session_favorite.repository as repository_module
from engine.community.core.session_favorite.repository import SessionFavoriteRepository


def test_add_list_and_remove_are_scoped_to_user(tmp_path):
    repository = SessionFavoriteRepository(tmp_path / "favorites.sqlite3")

    repository.add("user-a", "session-1")
    repository.add("user-a", "session-2")
    repository.add("user-b", "session-1")

    assert repository.list_session_ids("user-a") == ["session-2", "session-1"]
    assert repository.list_session_ids("user-b") == ["session-1"]

    assert repository.remove("user-a", "session-1") is True
    assert repository.remove("user-a", "session-1") is False
    assert repository.list_session_ids("user-a") == ["session-2"]
    assert repository.list_session_ids("user-b") == ["session-1"]


def test_add_is_idempotent(tmp_path):
    repository = SessionFavoriteRepository(tmp_path / "favorites.sqlite3")

    repository.add("user-a", "session-1")
    repository.add("user-a", "session-1")

    assert repository.list_session_ids("user-a") == ["session-1"]


def test_remove_session_cleans_all_users(tmp_path):
    repository = SessionFavoriteRepository(tmp_path / "favorites.sqlite3")
    repository.add("user-a", "session-1")
    repository.add("user-b", "session-1")
    repository.add("user-a", "session-2")

    assert repository.remove_session("session-1") == 2
    assert repository.list_session_ids("user-a") == ["session-2"]
    assert repository.list_session_ids("user-b") == []


def test_default_database_path_supports_adapter_state_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("ENGINE_ADAPTER_STATE_DIR", str(tmp_path))

    assert repository_module._default_database_path() == tmp_path / "session_favorites.sqlite3"

    monkeypatch.delenv("ENGINE_ADAPTER_STATE_DIR")
    assert repository_module._default_database_path().name == "session_favorites.sqlite3"


def test_repository_singleton_uses_configured_adapter_state_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(repository_module, "_repository", None)
    monkeypatch.setenv("ENGINE_ADAPTER_STATE_DIR", str(tmp_path))

    first = repository_module.get_session_favorite_repository()
    second = repository_module.get_session_favorite_repository()

    assert first is second
    assert first._database_path == tmp_path / "session_favorites.sqlite3"


def test_connection_is_closed_after_operation(monkeypatch, tmp_path):
    connection = MagicMock()
    monkeypatch.setattr(repository_module.sqlite3, "connect", lambda *_args, **_kwargs: connection)
    repository = SessionFavoriteRepository(tmp_path / "favorites.sqlite3")

    with repository._connect() as actual_connection:
        assert actual_connection is connection

    connection.close.assert_called_once()


def test_connection_is_closed_when_operation_fails(monkeypatch, tmp_path):
    connection = MagicMock()
    monkeypatch.setattr(repository_module.sqlite3, "connect", lambda *_args, **_kwargs: connection)
    repository = SessionFavoriteRepository(tmp_path / "favorites.sqlite3")

    with pytest.raises(RuntimeError, match="write failed"):
        with repository._connect():
            raise RuntimeError("write failed")

    connection.close.assert_called_once()
