"""The directory-list route distinguishes confirmed files from device failures."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from starlette.requests import Request

from agentclaw.community.adapters.http.openapi_v1.contracts import PageParams
from agentclaw.community.adapters.http.openapi_v1.resources.router import list_resources
from agentclaw.community.core.devices.services.device_context import DeviceOfflineError
from agentclaw.community.core.ports.resource_file_port import ListingFailed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "listing_status", "probe_result", "expected"),
    [
        ("notes/data.json", 400, False, "bad_path"),
        ("notes", 200, None, "page"),
        ("missing", 404, None, "page"),
        ("notes", 500, None, "upstream_error"),
        ("notes", 400, True, "upstream_error"),
        ("missing", 400, None, "upstream_error"),
        ("notes", 400, ListingFailed("Parent unavailable"), "upstream_error"),
        ("notes", 400, DeviceOfflineError("Device went offline"), "upstream_error"),
        ("", 400, None, "upstream_error"),
    ],
)
async def test_list_classifies_file_only_after_a_bad_directory_response(
    path, listing_status, probe_result, expected, caplog,
):
    upstream_request = httpx.Request("POST", "http://device.test/api/file/list")
    upstream_response = httpx.Response(listing_status, request=upstream_request)
    upstream_error = httpx.HTTPStatusError(
        "Device list failed", request=upstream_request, response=upstream_response
    )
    file_svc = SimpleNamespace(
        list_dir=AsyncMock(
            return_value=[],
            side_effect=upstream_error if listing_status != 200 else None,
        ),
        lookup_path_is_dir=AsyncMock(
            return_value=probe_result,
            side_effect=probe_result if isinstance(probe_result, Exception) else None,
        ),
    )
    runtime_context = object()
    bot_repo = Mock(get_by_id_and_owner=Mock(return_value={"active_engine": "aicoding"}))

    async def list_path():
        return await list_resources(
            page=PageParams(),
            user_id="u1",
            owner_id="u1",
            bot_id="bot-1",
            path=path,
            type=None,
            request=Request({"type": "http", "headers": [], "query_string": b""}),
            target={"runtime_context": runtime_context},
            bot_repo=bot_repo,
            file_svc=file_svc,
        )

    if expected == "bad_path":
        response = await list_path()
        assert response.status_code == 400
        assert json.loads(response.body)["message"] == "path must refer to a directory"
    elif expected == "page":
        response = await list_path()
        assert response.data.total == 0
    else:
        response = await list_path()
        assert response.status_code == 502
        assert json.loads(response.body) == {
            "code": 502000,
            "message": "Upstream service error",
            "data": None,
            "request_id": "",
        }
        assert any(
            record.exc_info is not None and record.exc_info[1] is upstream_error
            for record in caplog.records
        )

    file_svc.list_dir.assert_awaited_once()
    if path and listing_status == 400:
        file_svc.lookup_path_is_dir.assert_awaited_once_with(
            **file_svc.list_dir.await_args.kwargs
        )
    else:
        file_svc.lookup_path_is_dir.assert_not_awaited()
