"""The async HTTP handler must not execute synchronous catalog I/O on its loop."""

from threading import get_ident
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from agentclaw.community.adapters.http.openapi_v1.bot_public.router import search_public_bots
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope, get_current_avernet_tenant


@pytest.mark.asyncio
async def test_catalog_search_offloads_sync_service_and_copies_request_tenant():
    loop_thread = get_ident()
    observed = []

    class Service:
        def search_catalog_public_bots_by_keyword(self, **kwargs):
            observed.append((get_ident(), get_current_avernet_tenant(), kwargs))
            return {"total": 0, "items": []}

    request = Request({"type": "http", "headers": [], "state": {"trace_id": "catalog-test"}})
    with avernet_tenant_scope("tenant-a"):
        response = await search_public_bots(
            request=request, principal=SimpleNamespace(tenant="tenant-a", user_id="viewer", app_id=None),
            search=None, page_number=1, page_size=24, visibility=None, user_visibility=None,
            status=None, viewer_actor_type="human", viewer_actor_id="viewer", friendship=None,
            service=Service(),
        )
    assert response.data.total == 0
    assert observed[0][0] != loop_thread
    assert observed[0][1] == "tenant-a"
    assert observed[0][2]["caller"].tenant_id == "tenant-a"
