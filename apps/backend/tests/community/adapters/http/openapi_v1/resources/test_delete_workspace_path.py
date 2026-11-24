"""OpenAPI delete exercises the real BaaS file adapter without reading bytes."""

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
from agentclaw.community.core.services.resource_file_service import ResourceFileService


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entry", "listing_status", "status", "delete_route"),
    [
        ({"name": "sql", "is_dir": True}, 200, 200, "/api/file/rmtree"),
        ({"name": "sql", "is_dir": False}, 200, 200, "/api/file/remove"),
        ({"name": "sql.bak", "is_dir": True}, 200, 404, None),
        (None, 404, 404, None),
        (None, 500, 500, None),
        ({"name": "sql"}, 200, 502, None),
    ],
)
async def test_delete_uses_one_parent_listing(entry, listing_status, status, delete_route):
    calls = []

    def post(route, *, json):
        calls.append((route, json))
        request = httpx.Request("POST", f"http://device.test{route}")
        if route == "/api/file/read":
            return httpx.Response(
                500, json={"detail": "Is a directory (os error 21)"}, request=request
            )
        if route == "/api/file/list":
            return httpx.Response(
                listing_status,
                json={"data": {"files": [entry] if entry is not None else []}},
                request=request,
            )
        assert route == delete_route
        return httpx.Response(200, request=request)

    device_fs = BaasDeviceFileSystem(
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

    expected = [("/api/file/list", {"dir_path": "workspace/scripts", "recursive": False})]
    if delete_route is not None:
        expected.append((delete_route, {"target_path": "workspace/scripts/sql"}))
        result = await delete()
        assert result.data.deleted
        records.delete_file_record.assert_awaited_once_with(path="scripts/sql")
    else:
        if status == 500:
            with pytest.raises(httpx.HTTPStatusError) as error:
                await delete()
            assert error.value.response.status_code == status
        else:
            with pytest.raises(HTTPException) as error:
                await delete()
            assert error.value.status_code == status
        records.delete_file_record.assert_not_awaited()
    assert calls == expected
