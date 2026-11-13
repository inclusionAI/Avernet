from types import SimpleNamespace
from unittest.mock import AsyncMock
import inspect

import pytest
from fastapi import HTTPException

from agentclaw.community.adapters.http.resources import file_router
from agentclaw.community.core.devices.errors import DeviceFileSystemReadOnlyError


@pytest.mark.asyncio
async def test_mkdir_classifies_only_explicit_device_read_only(monkeypatch):
    monkeypatch.setattr(file_router, "_resolve_params", lambda *args, **kwargs: ("owner", "bot", "openclaw"))
    handler = inspect.unwrap(file_router.create_directory)
    service = SimpleNamespace(create_directory=AsyncMock(side_effect=DeviceFileSystemReadOnlyError()))
    with pytest.raises(HTTPException) as error:
        await handler(path="x", bot_id="bot", engine_type=None, owner_id="owner",
                      ctx=SimpleNamespace(), bot_repo=object(), file_svc=service)
    assert error.value.status_code == 403
    assert error.value.detail == {"code": "filesystem_read_only"}
