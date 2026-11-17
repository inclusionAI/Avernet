"""Book UI wire operations through real membership service and production DI.

Only ECB's external HTTP transport and authenticated session boundary are fixtures.
No internal content service or space authorization is replaced.
"""

from types import SimpleNamespace

import httpx
import pytest
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.knowledge_platform.content import (
    RemoteKnowledgeContentService,
)
from agentclaw.community.adapters.knowledge_platform.content_ports import (
    AsyncContentTransport,
    ContentAccessPolicy,
)
from agentclaw.community.api.knowledge_content_service import (
    KnowledgeContentServiceProtocol,
)
from agentclaw.community.plugins.http_client import AsyncHttpxClient
from fastapi import HTTPException
from fastapi.testclient import TestClient
from injector import InstanceProvider

from tests.community.endpoints.test_knowledge_management import (
    ACTOR,
    _assert_real_composition,
    _seed_enabled,
)
from tests.community.framework.fixtures import (
    app_with_testing_modules as app_with_testing_modules,
)
from tests.community.framework.world import World

from .test_content_http import prepare_test_access


@pytest.fixture
def book_ui(app_with_testing_modules):
    app = app_with_testing_modules
    world = World(app.state.injector)
    _seed_enabled(world)
    seen = []
    response = {"status": 200, "body": {"items": [], "total": 0}}

    def send(request):
        seen.append(request)
        return httpx.Response(response["status"], json=response["body"])

    # Use the full framework graph; only outbound HTTP and session policy vary.
    for interface, value in [
        (AsyncContentTransport, AsyncHttpxClient(transport=httpx.MockTransport(send))),
        (ContentAccessPolicy, prepare_test_access),
    ]:
        world.injector.binder.bind(interface, to=InstanceProvider(value))
    previous_overrides = dict(app.dependency_overrides)
    # Match the endpoint runner: the framework fixture owns lifecycle/DB teardown.
    client = TestClient(app)
    try:
        app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
            ACTOR, ACTOR, ACTOR
        )
        assert isinstance(
            world.get(KnowledgeContentServiceProtocol), RemoteKnowledgeContentService
        )
        yield SimpleNamespace(
            client=client,
            app=app,
            seen=seen,
            response=response,
            prefix="/api/knowledge/spaces/1/knowledge-bases/kb",
            world=world,
        )
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)


def test_classified_search_and_filtered_sources_use_fixed_upstream(book_ui):
    w = book_ui
    for source in ("personal", "team"):
        result = w.client.get(
            w.prefix + "/yuque/books",
            params={"source": source, "q": "Book", "offset": 3, "limit": 5},
        )
        assert result.status_code == 200
        request = w.seen[-1]
        assert request.url.path == "/api/v1/knowledge/kb/kb/yuque/books"
        assert dict(request.url.params) == {
            "source": source,
            "q": "Book",
            "offset": "3",
            "limit": "5",
        }
    result = w.client.get(w.prefix + "/sources?exclude_book_documents=true")
    assert result.status_code == 200
    assert w.seen[-1].url.params["exclude_book_documents"] == "true"
    assert w.world.injector is w.app.state.injector
    _assert_real_composition(result, w.world)


def test_old_query_defaults_do_not_opt_into_new_behavior(book_ui):
    w = book_ui
    assert w.client.get(w.prefix + "/yuque/books").status_code == 200
    assert "source" not in w.seen[-1].url.params
    assert w.client.get(w.prefix + "/sources").status_code == 200
    assert "exclude_book_documents" not in w.seen[-1].url.params
    count = len(w.seen)
    assert w.client.get(w.prefix + "/yuque/books?source=all").status_code == 422
    assert len(w.seen) == count


def test_delete_is_one_accepted_request_not_child_fanout(book_ui):
    w = book_ui
    w.response.update(
        status=202, body={"run_id": "run_remove_opaque", "status": "QUEUED"}
    )
    result = w.client.delete(
        w.prefix + "/yuque/book-imports/ds",
        headers={"Cookie": "session=fixture; unrelated=secret"},
    )
    assert result.status_code == 202
    assert result.json() == w.response["body"]
    assert len(w.seen) == 1
    request = w.seen[0]
    assert request.method == "DELETE"
    assert request.url.path == "/api/v1/knowledge/kb/kb/yuque/book-imports/ds"
    assert request.content == b""
    assert request.headers["cookie"] == "session=fixture"
    assert "secret" not in result.text


def test_current_run_and_deletion_status_are_provider_owned(book_ui):
    w = book_ui
    for suffix, body in [
        (
            "",
            {
                "data_source_id": "ds",
                "current_run": {
                    "run_id": "r",
                    "status": "PARTIAL",
                    "succeeded": 2,
                    "skipped": 1,
                    "failed": 1,
                    "total": 4,
                },
            },
        ),
        (
            "/deletion",
            {"run_id": "remove", "status": "RUNNING", "deleted": 2, "retained": 1},
        ),
    ]:
        w.response["body"] = body
        result = w.client.get(w.prefix + "/yuque/book-imports/ds" + suffix)
        assert result.status_code == 200 and result.json() == body
        assert (
            w.seen[-1].url.path
            == "/api/v1/knowledge/kb/kb/yuque/book-imports/ds" + suffix
        )


@pytest.mark.parametrize("status", [403, 404, 409, 503])
def test_native_delete_failures_are_not_success_or_retried(book_ui, status):
    w = book_ui
    w.response.update(status=status, body={"detail": "native denial"})
    result = w.client.delete(w.prefix + "/yuque/book-imports/ds")
    assert result.status_code == status
    assert len(w.seen) == 1


def test_real_membership_and_auth_gates_deny_before_ecb(book_ui):
    w = book_ui
    w.app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        "outsider", "outsider", "outsider"
    )
    assert w.client.delete(w.prefix + "/yuque/book-imports/ds").status_code == 403
    assert not w.seen

    def unauthenticated():
        raise HTTPException(401, "Unauthenticated")

    w.app.dependency_overrides[get_current_user] = unauthenticated
    assert w.client.delete(w.prefix + "/yuque/book-imports/ds").status_code == 401
    assert not w.seen
