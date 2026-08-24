from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from engine.community.config import ChatFileShareSettings
from engine.community.core.chat_file_share.models import ChatFileShareResult
from engine.community.core.chat_file_share.service import ChatFileShareService


class _Service:
    async def share(
        self,
        *,
        relative_path: str,
        session_key: str,
    ) -> ChatFileShareResult:
        raise AssertionError("the lifespan test does not invoke sharing")


class _Injector:
    def get(self, dependency):
        assert dependency is ChatFileShareService
        return _Service()


class _Manager:
    def __init__(self) -> None:
        self.initialize = AsyncMock()
        self.shutdown = AsyncMock()


@pytest.mark.asyncio
async def test_lifespan_starts_and_removes_the_private_local_share_socket(
    monkeypatch,
) -> None:
    from engine.community.api import app as app_module

    socket_dir = Path(tempfile.mkdtemp(prefix="tcfs-", dir="/tmp"))
    socket_path = socket_dir / "private" / "file-share.sock"
    settings = ChatFileShareSettings(
        socket_path=socket_path,
        baas_base_url="https://baas.example",
        tenant="team_claw",
        allowed_share_hosts=frozenset({"oss.example"}),
    )
    manager = _Manager()
    monkeypatch.setattr(app_module, "_INJECTOR", _Injector())
    monkeypatch.setattr(
        app_module,
        "load_chat_file_share_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        app_module.EngineManager,
        "get_instance",
        classmethod(lambda cls: manager),
    )
    monkeypatch.setattr(
        app_module.EngineManager,
        "bind_injector",
        classmethod(lambda cls, injector: None),
    )

    try:
        async with app_module.lifespan(FastAPI()):
            assert socket_path.exists()
            assert socket_path.stat().st_mode & 0o777 == 0o600

        assert not socket_path.exists()
        manager.initialize.assert_awaited_once()
        manager.shutdown.assert_awaited_once()
    finally:
        shutil.rmtree(socket_dir, ignore_errors=True)
