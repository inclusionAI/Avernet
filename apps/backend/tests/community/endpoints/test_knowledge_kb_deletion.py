"""Lifecycle forwarding through framework DI and real local membership checks."""

import httpx
from injector import InstanceProvider
from agentclaw.community.adapters.knowledge_platform.content_ports import (
    AsyncContentTransport,
)
from agentclaw.community.plugins.http_client import AsyncHttpxClient
from tests.community.endpoints.test_knowledge_management import (
    ACTOR,
    KB,
    _assert_real_composition,
    _seed_enabled,
)
from tests.community.factories.access import make_staff_user
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_PENDING = {
    "kb_id": "kb",
    "status": "pending_delete",
    "cleanup_status": "waiting",
    "delete_at_ms": 1795000000000,
}
_DELETED = {"kb_id": "kb", "status": "deleted", "cleanup_status": "pending"}
_ACTIVE = {"kb_id": "kb", "status": "active"}


class DeletionWire(httpx.MockTransport):
    def __init__(self):
        self.calls = []
        super().__init__(self.answer)

    def answer(self, request):
        self.calls.append(request)
        assert request.headers["X-Test-Session"] == "fixture"
        assert not request.content
        if request.method == "DELETE":
            assert request.url.path == "/api/v1/knowledge/kb/kb"
            assert dict(request.url.params) in ({}, {"immediate": "true"})
            return httpx.Response(200, json=_DELETED if request.url.query else _PENDING)
        assert (request.method, request.url.path) == (
            "POST",
            "/api/v1/knowledge/kb/kb/cancel-delete",
        )
        assert not request.url.query
        return httpx.Response(200, json=_ACTIVE)


def seed(world):
    _seed_enabled(world)
    wire = DeletionWire()
    world.injector.binder.bind(DeletionWire, to=InstanceProvider(wire))
    world.injector.binder.bind(
        AsyncContentTransport, to=InstanceProvider(AsyncHttpxClient(transport=wire))
    )


def outsider(world):
    seed(world)
    make_staff_user(world, user_id="delete-outsider")


def one_call(response, world):
    _assert_real_composition(response, world)
    assert len(world.get(DeletionWire).calls) == 1


def no_call(response, world):
    _assert_real_composition(response, world)
    assert not world.get(DeletionWire).calls


for method, path, query, payload in [
    ("DELETE", KB, {}, _PENDING),
    ("DELETE", KB, {"immediate": "true"}, _DELETED),
    ("POST", KB + "/cancel-delete", {}, _ACTIVE),
]:
    for scenario, actor, initializer, expected, assertion in [
        (
            "kb_delete_provider_owned",
            ACTOR,
            seed,
            ExpectSuccess(status=200, json_equals=payload),
            one_call,
        ),
        (
            "kb_delete_nonmember_denied",
            "delete-outsider",
            outsider,
            ExpectError(
                status=403, json_contains={"detail": {"code": "SPACE_MEMBER_REQUIRED"}}
            ),
            no_call,
        ),
    ]:
        endpoint_test(
            method=method,
            path=path,
            scenario=scenario + ("_immediate" if query else ""),
            input=CaseInput(
                path_params={"space_id": 1, "kb_id": "kb"},
                query_params=query,
                headers={"x-user-id": actor, "cookie": "test_session=fixture"},
            ),
            seed=initializer,
            expect=expected,
            extra_assertions=(assertion,),
        )(lambda: None)
