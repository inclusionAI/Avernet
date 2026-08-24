from __future__ import annotations

from pathlib import Path

import pytest

from injector import Injector

from engine.community.core.chat_file_share.service import ChatFileShareService
from engine.community.di.modules.chat_file_share_module import ChatFileShareModule
from engine.community.plugins.session_file_export import BaasSessionFileClient


def _configure_profile(monkeypatch, workspace_root: Path) -> None:
    monkeypatch.setenv("OPENCLAW_WORKSPACE_DIR", str(workspace_root))
    monkeypatch.setenv("ENGINE_CHAT_FILE_SHARE_SOCKET", "/tmp/teamclaw-file-share.sock")
    monkeypatch.setenv("ENGINE_CHAT_FILE_SHARE_BAAS_BASE_URL", "https://baas.example")
    monkeypatch.setenv("ENGINE_CHAT_FILE_SHARE_TENANT", "tenant-a")
    monkeypatch.setenv("ENGINE_CHAT_FILE_SHARE_ALLOWED_OSS_HOSTS", "oss.example")


def test_module_does_not_bind_a_client_when_local_sharing_is_disabled(
    monkeypatch,
) -> None:
    monkeypatch.delenv("ENGINE_CHAT_FILE_SHARE_SOCKET", raising=False)

    injector = Injector([ChatFileShareModule()])

    with pytest.raises(RuntimeError, match="requires Engine profile and workspace"):
        injector.get(ChatFileShareService)


def test_module_builds_a_dedicated_session_file_client_for_chat_sharing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _configure_profile(monkeypatch, tmp_path)

    service = Injector([ChatFileShareModule()]).get(ChatFileShareService)

    assert isinstance(service, ChatFileShareService)
    assert isinstance(service._client, BaasSessionFileClient)


def test_module_requires_a_workspace_when_file_sharing_is_enabled(monkeypatch) -> None:
    monkeypatch.delenv("OPENCLAW_WORKSPACE_DIR", raising=False)
    _configure_profile(monkeypatch, Path("/unavailable"))
    monkeypatch.delenv("OPENCLAW_WORKSPACE_DIR", raising=False)

    with pytest.raises(RuntimeError, match="requires Engine profile and workspace"):
        Injector([ChatFileShareModule()]).get(ChatFileShareService)
