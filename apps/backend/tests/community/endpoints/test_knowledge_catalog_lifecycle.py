"""Lifecycle metadata survives the actual catalog/service/HTTP provider chain."""

from contextlib import contextmanager
from typing import Annotated

import httpx
from agentclaw.community.plugin_api.http_client import HttpClient

from tests.community.endpoints.test_knowledge_management import (
    ACTOR,
    _assert_real_composition,
    _seed_enabled,
)
from tests.community.framework import CaseInput, ExpectSuccess, endpoint_test
from tests.community.knowledge_management.http_contract import detail


class CatalogWire(httpx.MockTransport):
    def __init__(self, rows):
        self.rows, self.calls = rows, []
        super().__init__(self.answer)

    def answer(self, request):
        self.calls.append(request)
        assert request.method == "GET"
        assert request.url.path == (
            "/api/v1/knowledge/integrations/tc/spaces/1/knowledge-bases"
        )
        assert dict(request.url.params) == {
            "actor_staff_no": ACTOR,
            "cursor": "previous-page",
            "limit": "2",
        }
        return httpx.Response(
            200, json={"items": self.rows, "next_cursor": "next-page"}
        )


def catalog_seed(lifecycle):
    def seed(world):
        _seed_enabled(world)
        # Include a READ-only row: cancellation capability is not can_manage.
        wire = CatalogWire(
            [
                detail(kb="kb", **lifecycle),
                detail(
                    kb="read-only",
                    can_manage=False,
                    **(lifecycle | {"can_cancel_delete": False} if lifecycle else {}),
                ),
            ]
        )
        world.injector.binder.bind(CatalogWire, to=wire)

        @contextmanager
        def send(method, url, **kwargs):
            kwargs.pop("timeout", None)
            response = wire.handle_request(httpx.Request(method, url, **kwargs))
            try:
                yield response
            finally:
                response.close()

        world.get(Annotated[HttpClient, "knowledge"]).set_override("stream", send)

    return seed


def lifecycle_page(response, world):
    _assert_real_composition(response, world)
    wire = world.get(CatalogWire)
    assert len(wire.calls) == 1
    body = response.json()
    assert body["items"] == body["knowledge_bases"]
    for result, source in zip(body["items"], wire.rows, strict=True):
        assert result == {**source, "space_name": result["space_name"]}


for scenario, lifecycle in [
    ("active", {"status": "active", "delete_at_ms": None, "can_cancel_delete": False}),
    (
        "pending_cancellable",
        {
            "status": "pending_delete",
            "delete_at_ms": 1795000000123,
            "can_cancel_delete": True,
        },
    ),
    (
        "pending_not_cancellable",
        {
            "status": "pending_delete",
            "delete_at_ms": 1795000000123,
            "can_cancel_delete": False,
        },
    ),
    ("legacy_omitted", {}),
]:
    endpoint_test(
        method="GET",
        path="/api/knowledge/spaces/{space_id}/knowledge-bases",
        scenario="catalog_lifecycle_" + scenario,
        input=CaseInput(
            path_params={"space_id": 1},
            query_params={"cursor": "previous-page", "limit": "2"},
            headers={"x-user-id": ACTOR},
        ),
        seed=catalog_seed(lifecycle),
        expect=ExpectSuccess(status=200, json_contains={"next_cursor": "next-page"}),
        extra_assertions=(lifecycle_page,),
    )(lambda: None)
