from __future__ import annotations

import pytest

from engine.community.config import load_chat_file_share_settings


@pytest.fixture(autouse=True)
def _clear_file_share_env(monkeypatch):
    for name in (
        "ENGINE_CHAT_FILE_SHARE_SOCKET",
        "ENGINE_CHAT_FILE_SHARE_BAAS_BASE_URL",
        "ENGINE_CHAT_FILE_SHARE_TENANT",
        "ENGINE_CHAT_FILE_SHARE_ALLOWED_OSS_HOSTS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_file_share_is_disabled_without_a_socket_path():
    assert load_chat_file_share_settings() is None


def test_file_share_loads_the_session_file_profile(monkeypatch, tmp_path):
    socket_path = tmp_path / "file-share.sock"
    monkeypatch.setenv("ENGINE_CHAT_FILE_SHARE_SOCKET", str(socket_path))
    monkeypatch.setenv("ENGINE_CHAT_FILE_SHARE_BAAS_BASE_URL", "https://baas.example")
    monkeypatch.setenv("ENGINE_CHAT_FILE_SHARE_TENANT", "team_claw")
    monkeypatch.setenv(
        "ENGINE_CHAT_FILE_SHARE_ALLOWED_OSS_HOSTS",
        "oss-a.example, oss-b.example ",
    )

    settings = load_chat_file_share_settings()

    assert settings is not None
    assert settings.socket_path == socket_path
    assert settings.tenant == "team_claw"
    assert settings.allowed_share_hosts == frozenset(
        {"oss-a.example", "oss-b.example"}
    )


def test_file_share_rejects_a_partial_profile_without_echoing_values(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("ENGINE_CHAT_FILE_SHARE_SOCKET", str(tmp_path / "share.sock"))
    monkeypatch.setenv("ENGINE_CHAT_FILE_SHARE_TENANT", "team_claw")

    with pytest.raises(ValueError, match="ENGINE_CHAT_FILE_SHARE_BAAS_BASE_URL"):
        load_chat_file_share_settings()


def test_file_share_requires_an_allowlisted_share_host(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ENGINE_CHAT_FILE_SHARE_SOCKET", str(tmp_path / "share.sock"))
    monkeypatch.setenv("ENGINE_CHAT_FILE_SHARE_BAAS_BASE_URL", "https://baas.example")
    monkeypatch.setenv("ENGINE_CHAT_FILE_SHARE_TENANT", "team_claw")

    with pytest.raises(
        ValueError,
        match="ENGINE_CHAT_FILE_SHARE_ALLOWED_OSS_HOSTS",
    ):
        load_chat_file_share_settings()


def test_file_share_requires_an_absolute_socket_path(monkeypatch) -> None:
    monkeypatch.setenv("ENGINE_CHAT_FILE_SHARE_SOCKET", "relative-file-share.sock")
    monkeypatch.setenv("ENGINE_CHAT_FILE_SHARE_BAAS_BASE_URL", "https://baas.example")
    monkeypatch.setenv("ENGINE_CHAT_FILE_SHARE_TENANT", "team_claw")
    monkeypatch.setenv("ENGINE_CHAT_FILE_SHARE_ALLOWED_OSS_HOSTS", "oss.example")
    with pytest.raises(ValueError, match="SOCKET must be absolute"):
        load_chat_file_share_settings()
