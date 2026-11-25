"""KB deletion stays a single provider-owned operation through native DI."""

import pytest
from fastapi import HTTPException
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser

from tests.community.framework.fixtures import (
    app_with_testing_modules as app_with_testing_modules,
)

from .test_book_ui_contract import book_ui as book_ui


@pytest.mark.parametrize(
    "query,params,status",
    [
        ("", {}, "pending_delete"),
        ("?immediate=false", {}, "pending_delete"),
        ("?immediate=true", {"immediate": "true"}, "deleted"),
    ],
)
def test_delete_mode_and_pending_intent_are_forwarded_once(
    book_ui, query, params, status
):
    w = book_ui
    w.response["body"] = {
        "kb_id": "kb",
        "status": status,
        "cleanup_status": "waiting" if status == "pending_delete" else "pending",
        "delete_at_ms": 1795000000000,
        "warnings": ["unfinished work will stop"],
    }
    response = w.client.delete(w.prefix + query)
    assert response.status_code == 200 and response.json() == w.response["body"]
    assert len(w.seen) == 1
    request = w.seen[0]
    assert (request.method, request.url.path) == ("DELETE", "/api/v1/knowledge/kb/kb")
    assert dict(request.url.params) == params and request.content == b""


def test_cancel_does_not_require_active_catalog_or_registry_preflight(book_ui):
    w = book_ui
    w.response["body"] = {"kb_id": "kb", "status": "active"}
    response = w.client.post(w.prefix + "/cancel-delete")
    assert response.status_code == 200 and response.json() == w.response["body"]
    assert len(w.seen) == 1
    request = w.seen[0]
    assert (request.method, request.url.path) == (
        "POST",
        "/api/v1/knowledge/kb/kb/cancel-delete",
    )
    assert request.content == b"" and not request.url.query


@pytest.mark.parametrize("operation", ["delete", "cancel"])
@pytest.mark.parametrize("status", [401, 403, 404, 409, 503])
def test_provider_denial_is_not_retried_or_reinterpreted(book_ui, operation, status):
    w = book_ui
    w.response.update(status=status, body={"detail": "native refusal"})
    response = (
        w.client.delete(w.prefix)
        if operation == "delete"
        else w.client.post(w.prefix + "/cancel-delete")
    )
    assert response.status_code == status
    expected = (
        {"detail": {"code": "KNOWLEDGE_DEPENDENCY_UNAVAILABLE"}}
        if status == 503
        else w.response["body"]
    )
    assert response.json() == expected
    assert len(w.seen) == 1


@pytest.mark.parametrize("operation", ["delete", "cancel"])
def test_auth_and_membership_reject_before_any_provider_request(book_ui, operation):
    w = book_ui
    w.app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        "outsider", "outsider", "outsider"
    )

    def call():
        if operation == "delete":
            return w.client.delete(w.prefix)
        return w.client.post(w.prefix + "/cancel-delete")

    assert call().status_code == 403 and not w.seen

    def unauthenticated():
        raise HTTPException(401, "Unauthenticated")

    w.app.dependency_overrides[get_current_user] = unauthenticated
    assert call().status_code == 401 and not w.seen


def test_invalid_immediate_never_reaches_provider(book_ui):
    assert (
        book_ui.client.delete(book_ui.prefix + "?immediate=invalid").status_code == 422
    )
    assert not book_ui.seen
