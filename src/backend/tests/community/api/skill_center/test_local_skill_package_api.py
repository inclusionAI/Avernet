from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Request, Response

from agentclaw.community.adapters.http.dependencies import RequestContext
from agentclaw.community.adapters.http.skill_center.skills import (
    download_local_skill_package,
    replace_local_skill_package,
)


def _request(*, content_type: str = "application/zip", if_match: str = "") -> Request:
    headers = [(b"content-type", content_type.encode())]
    if if_match:
        headers.append((b"if-match", if_match.encode()))
    return Request({"type": "http", "method": "PUT", "path": "/", "headers": headers})


@pytest.mark.asyncio
async def test_download_exports_complete_local_package_for_authenticated_owner():
    digest = f"sha256:{'a' * 64}"
    query_service = MagicMock()
    query_service.get_local_package = AsyncMock(return_value=(b"zip-bytes", digest))

    result = await download_local_skill_package(
        skill_id="17",
        bot_id="bot-1",
        ctx=RequestContext(user_id="user-1", bot_id="bot-1"),
        query_service=query_service,
    )

    assert result.body == b"zip-bytes"
    assert result.headers["x-skill-package-sha256"] == digest
    query_service.get_skill.assert_called_once_with(
        skill_id="17",
        bot_id="bot-1",
        owner_id="user-1",
        user_id="user-1",
    )


@pytest.mark.asyncio
async def test_replace_uses_cas_and_authenticated_owner():
    previous = f"sha256:{'a' * 64}"
    current = f"sha256:{'b' * 64}"
    query_service = MagicMock()
    upload_service = MagicMock()
    upload_service.replace_local_skill_package = AsyncMock(
        return_value={"package_digest": current}
    )
    response = Response()

    result = await replace_local_skill_package(
        skill_id="17",
        request=_request(if_match=previous),
        response=response,
        package=b"candidate-zip",
        bot_id="bot-1",
        ctx=RequestContext(user_id="user-1", bot_id="bot-1"),
        query_service=query_service,
        upload_service=upload_service,
    )

    assert result == {"success": True, "data": {"sha256": current}}
    assert response.headers["x-skill-package-sha256"] == current
    upload_service.replace_local_skill_package.assert_awaited_once_with(
        skill_id="17",
        bot_id="bot-1",
        owner_id="user-1",
        actor_id="user-1",
        package=b"candidate-zip",
        expected_digest=previous,
    )


@pytest.mark.asyncio
async def test_replace_rejects_non_zip_content_type_before_write():
    with pytest.raises(HTTPException) as error:
        await replace_local_skill_package(
            skill_id="17",
            request=_request(content_type="application/json"),
            response=Response(),
            package=b"not-a-zip",
            bot_id="bot-1",
            ctx=RequestContext(user_id="user-1", bot_id="bot-1"),
            query_service=MagicMock(),
            upload_service=MagicMock(),
        )

    assert error.value.status_code == 415
