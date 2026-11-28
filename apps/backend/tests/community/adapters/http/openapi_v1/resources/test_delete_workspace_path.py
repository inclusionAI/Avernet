"""OpenAPI delete exercises the real device file adapters without reading bytes."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from agentclaw.community.adapters.http.openapi_v1.resources.router import delete_file
from agentclaw.community.core.devices.services.baas_device_filesystem import (
    BaasDeviceFileSystem,
)
from agentclaw.community.core.devices.services.teclaw_device_filesystem import (
    TeclawDeviceFileSystem,
)
from agentclaw.community.core.services.resource_file_service import ResourceFileService


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entry", "listing_status", "status", "delete_route", "file_system"),
    [
        ({"name": "sql", "is_dir": True}, 200, 200, "/api/file/rmtree", BaasDeviceFileSystem),
        ({"name": "sql", "is_dir": False}, 200, 200, "/api/file/remove", BaasDeviceFileSystem),
        ({"name": "sql.bak", "is_dir": True}, 200, 404, None, BaasDeviceFileSystem),
        (None, 404, 404, None, BaasDeviceFileSystem),
        (None, 500, 502, None, BaasDeviceFileSystem),
        ({"name": "sql"}, 200, 502, None, BaasDeviceFileSystem),
        ({"name": "sql", "is_dir": 2}, 200, 502, None, BaasDeviceFileSystem),
        (None, 500, 502, None, TeclawDeviceFileSystem),
        ({"name": "sql.bak", "is_dir": True}, 200, 404, None, TeclawDeviceFileSystem),
    ],
)
async def test_delete_uses_one_parent_listing(entry, listing_status, status, delete_route, file_system):
    calls = []
    list_route = "/api/v1/file/list" if file_system is TeclawDeviceFileSystem else "/api/file/list"

    def post(route, *, json, allow_device_failover=False):
        calls.append((route, json, allow_device_failover))
        request = httpx.Request("POST", f"http://device.test{route}")
        if route == "/api/file/read":
            return httpx.Response(
                500, json={"detail": "Is a directory (os error 21)"}, request=request
            )
        if route == list_route:
            return httpx.Response(
                listing_status,
                json={"data": {"files": [entry] if entry is not None else []}},
                request=request,
            )
        assert route == delete_route
        return httpx.Response(200, request=request)

    device_fs = file_system(
        transport=SimpleNamespace(post=post), conn_info={}, path_mapper=lambda path: path
    )
    service = ResourceFileService(
        publish_repo=Mock(),
        bot_repo=Mock(),
        resolver=Mock(),
        device_fs_dispatcher=Mock(dispatch_addressed=Mock(return_value=device_fs)),
    )
    records = SimpleNamespace(delete_file_record=AsyncMock())
    bot = {"active_engine": "aicoding"}

    async def delete():
        return await delete_file(
            user_id="u1",
            owner_id="u1",
            path="scripts/sql",
            request=Request({"type": "http", "headers": []}),
            bot_id="bot-1",
            factory=Mock(create=Mock(return_value=records)),
            bot_repo=Mock(get_by_id_and_owner=Mock(return_value=bot)),
            file_svc=service,
        )

    expected = [
        (
            list_route,
            {"dir_path": "workspace/scripts", "recursive": False},
            file_system is BaasDeviceFileSystem,
        )
    ]
    if delete_route is not None:
        expected.append((delete_route, {"target_path": "workspace/scripts/sql"}, False))
        result = await delete()
        assert result.data.deleted
        records.delete_file_record.assert_awaited_once_with(path="scripts/sql")
    else:
        if listing_status == 500 and file_system is BaasDeviceFileSystem:
            response = await delete()
            assert response.status_code == status
            assert json.loads(response.body) == {
                "code": 502000,
                "message": "Upstream service error",
                "data": None,
                "request_id": "",
            }
        else:
            with pytest.raises(HTTPException) as error:
                await delete()
            assert error.value.status_code == status
        records.delete_file_record.assert_not_awaited()
    assert calls == expected
