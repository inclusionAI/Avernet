"""Book removal endpoints through the framework's real DI and membership gates.

Only the external HTTP wire and deployment session policy are scripted. The
framework owns invocation; these cases do not certify provider-side cleanup.
"""

import httpx
from agentclaw.community.adapters.knowledge_platform.content_ports import (
    AsyncContentTransport,
)
from agentclaw.community.plugins.http_client import AsyncHttpxClient
from injector import InstanceProvider

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

_BOOK = KB + "/yuque/book-imports/{source_id}"
_COUNTS = {"deleted": 2, "retained": 1, "unknown": 0}
_ACCEPTED = {
    "data_source_id": "source",
    "run_id": "run_remove_opaque",
    "status": "PENDING",
    "accepted": True,
    "counts": {"deleted": 0, "retained": 0, "unknown": 0},
}
_PROGRESS = {**_ACCEPTED, "status": "RUNNING", "counts": _COUNTS}
_NOT_FOUND = {"detail": "YUQUE_BOOK_NOT_FOUND"}


class _BookWire(httpx.MockTransport):
    def __init__(self, *, missing=False):
        self.calls = []
        self.missing = missing
        super().__init__(self.answer)

    def answer(self, request):
        self.calls.append(request)
        assert request.headers["X-Test-Session"] == "fixture"
        assert (request.method, request.url.path) in {
            ("DELETE", "/api/v1/knowledge/kb/kb/yuque/book-imports/source"),
            ("GET", "/api/v1/knowledge/kb/kb/yuque/book-imports/source/deletion"),
        }
        assert not request.url.query and request.content == b""
        if self.missing:
            return httpx.Response(404, json=_NOT_FOUND)
        if request.method == "DELETE":
            return httpx.Response(202, json=_ACCEPTED)
        return httpx.Response(200, json=_PROGRESS)


def _seed_book(world, *, missing=False):
    _seed_enabled(world)
    wire = _BookWire(missing=missing)
    world.injector.binder.bind(_BookWire, to=InstanceProvider(wire))
    world.injector.binder.bind(
        AsyncContentTransport,
        to=InstanceProvider(AsyncHttpxClient(transport=wire)),
    )


def _seed_missing(world):
    _seed_book(world, missing=True)


def _seed_nonmember(world):
    _seed_book(world)
    make_staff_user(world, user_id="book-outsider")


def _assert_one_provider_call(response, world):
    _assert_real_composition(response, world)
    calls = world.get(_BookWire).calls
    assert len(calls) == 1
    assert calls[0].method == response.request.method
    assert calls[0].url.path == response.request.url.path.replace(
        "/api/knowledge/spaces/1/knowledge-bases/kb", "/api/v1/knowledge/kb/kb"
    )


def _assert_no_provider_call(response, world):
    _assert_real_composition(response, world)
    assert world.get(_BookWire).calls == []


for method, path, status, payload in (
    ("DELETE", _BOOK, 202, _ACCEPTED),
    ("GET", _BOOK + "/deletion", 200, _PROGRESS),
):
    for scenario, actor, seed, expect, assertion in (
        (
            "book_ui_provider_owned_success",
            ACTOR,
            _seed_book,
            ExpectSuccess(status=status, json_equals=payload),
            _assert_one_provider_call,
        ),
        (
            "book_ui_native_not_found",
            ACTOR,
            _seed_missing,
            ExpectError(status=404, json_contains=_NOT_FOUND),
            _assert_one_provider_call,
        ),
        (
            "book_ui_nonmember_denied_before_provider",
            "book-outsider",
            _seed_nonmember,
            ExpectError(
                status=403, json_contains={"detail": {"code": "SPACE_MEMBER_REQUIRED"}}
            ),
            _assert_no_provider_call,
        ),
    ):
        endpoint_test(
            method=method,
            path=path,
            scenario=scenario,
            input=CaseInput(
                path_params={"space_id": 1, "kb_id": "kb", "source_id": "source"},
                headers={"x-user-id": actor, "cookie": "test_session=fixture"},
            ),
            seed=seed,
            expect=expect,
            extra_assertions=(assertion,),
        )(lambda: None)
