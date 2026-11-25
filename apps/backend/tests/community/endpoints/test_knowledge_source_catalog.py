"""Unified list through real service DI; only the external HTTP wire is scripted."""

import httpx
from injector import InstanceProvider

from agentclaw.community.adapters.knowledge_platform.content_ports import (
    AsyncContentTransport,
)
from agentclaw.community.api.knowledge_content_service import (
    KnowledgeContentServiceProtocol,
)
from agentclaw.community.adapters.knowledge_platform.content import (
    RemoteKnowledgeContentService,
)
from agentclaw.community.plugins.http_client import AsyncHttpxClient
from tests.community.endpoints.test_knowledge_management import ACTOR, KB, _seed_enabled
from tests.community.factories.access import make_staff_user
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_PATH = KB + "/data-sources"
_PAGE = {
    "items": [
        {
            "id": "source:document",
            "kind": "document",
            "document": {"source_id": "document", "source_type": "upload"},
        },
        {
            "id": "data-source:book",
            "kind": "yuque_book",
            "book": {
                "data_source_id": "book",
                "task": {"status": "running", "total_count": 12},
            },
        },
        {
            "id": "source:yuque-document",
            "kind": "document",
            "document": {"source_id": "yuque-document", "source_type": "yuque"},
        },
    ],
    "total": 23,
    "offset": 20,
    "limit": 20,
    "next_offset": None,
}


class _CatalogWire(httpx.MockTransport):
    def __init__(self, status=200, offset=20):
        self.calls = []
        self.status = status
        self.offset = offset
        super().__init__(self.answer)

    def answer(self, request):
        self.calls.append(request)
        assert request.method == "GET"
        assert request.url.path == "/api/v1/knowledge/kb/kb/data-sources"
        assert dict(request.url.params) == {"offset": str(self.offset), "limit": "20"}
        assert request.headers["X-Test-Session"] == "fixture"
        assert not request.content
        return httpx.Response(
            self.status,
            json=(
                {
                    **_PAGE,
                    "offset": self.offset,
                    "next_offset": 20 if self.offset == 0 else None,
                }
                if self.status == 200
                else {
                    "detail": "KB_ACCESS_DENIED"
                    if self.status == 403
                    else "KB_NOT_FOUND"
                }
            ),
        )


def _seed(world, status=200, offset=20):
    _seed_enabled(world)
    wire = _CatalogWire(status, offset)
    world.injector.binder.bind(_CatalogWire, to=InstanceProvider(wire))
    world.injector.binder.bind(
        AsyncContentTransport, to=InstanceProvider(AsyncHttpxClient(transport=wire))
    )


def _default(world):
    _seed(world, offset=0)


def _denied(world):
    _seed(world, 403)


def _missing(world):
    _seed(world, 404)


def _outsider(world):
    _seed(world)
    make_staff_user(world, user_id="catalog-outsider")


def _called(response, world):
    service = world.get(KnowledgeContentServiceProtocol)
    assert isinstance(service, RemoteKnowledgeContentService)
    assert service.client.transport is world.get(AsyncContentTransport)
    assert len(world.get(_CatalogWire).calls) == 1


def _not_called(response, world):
    assert world.get(_CatalogWire).calls == []


for scenario, actor, seed, query, expect, assertion in (
    (
        "catalog_login_required",
        "",
        _seed,
        {},
        ExpectError(status=401),
        _not_called,
    ),
    (
        "catalog_default_pagination",
        ACTOR,
        _default,
        {},
        ExpectSuccess(json_equals={**_PAGE, "offset": 0, "next_offset": 20}),
        _called,
    ),
    (
        "catalog_native_kb_acl_denial",
        ACTOR,
        _denied,
        {"offset": 20},
        ExpectError(status=403, json_contains={"detail": "KB_ACCESS_DENIED"}),
        _called,
    ),
    (
        "catalog_native_page",
        ACTOR,
        _seed,
        {"offset": 20, "limit": 20},
        ExpectSuccess(json_equals=_PAGE),
        _called,
    ),
    (
        "catalog_native_not_found",
        ACTOR,
        _missing,
        {"offset": 20},
        ExpectError(status=404, json_contains={"detail": "KB_NOT_FOUND"}),
        _called,
    ),
    (
        "catalog_space_member_gate",
        "catalog-outsider",
        _outsider,
        {},
        ExpectError(
            status=403, json_contains={"detail": {"code": "SPACE_MEMBER_REQUIRED"}}
        ),
        _not_called,
    ),
    (
        "catalog_reject_negative_offset",
        ACTOR,
        _seed,
        {"offset": -1},
        ExpectError(status=422),
        _not_called,
    ),
    (
        "catalog_reject_large_limit",
        ACTOR,
        _seed,
        {"limit": 101},
        ExpectError(status=422),
        _not_called,
    ),
    (
        "catalog_reject_zero_limit",
        ACTOR,
        _seed,
        {"limit": 0},
        ExpectError(status=422),
        _not_called,
    ),
):
    endpoint_test(
        method="GET",
        path=_PATH,
        scenario=scenario,
        input=CaseInput(
            path_params={"space_id": 1, "kb_id": "kb"},
            query_params=query,
            headers={"x-user-id": actor, "cookie": "test_session=fixture"},
        ),
        seed=seed,
        expect=expect,
        extra_assertions=(assertion,),
    )(lambda: None)
