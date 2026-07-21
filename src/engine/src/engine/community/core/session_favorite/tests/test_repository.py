from __future__ import annotations

import engine.community.core.session_favorite.repository as repository_module
from engine.community.core.session_favorite.repository import SessionFavoriteRepository


def test_add_list_and_remove_are_scoped_to_user(tmp_path):
    repository = SessionFavoriteRepository(tmp_path / "favorites.sqlite3")

    repository.add("user-a", "session-1")
    repository.add("user-a", "session-2")
    repository.add("user-b", "session-1")

    assert repository.list_session_ids("user-a") == ["session-1", "session-2"]
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
