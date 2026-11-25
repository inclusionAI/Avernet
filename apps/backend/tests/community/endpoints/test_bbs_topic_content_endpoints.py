"""Endpoint-framework coverage for BBS Topic edit and delete operations."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.task.forum_service import ForumServiceProtocol
from agentclaw.community.core.task.forum.models import (
    ForumPostLocationRecord,
    ForumTopicAnalysisRecord,
    ForumTopicRecord,
)
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    bind_overrides,
    endpoint_test,
)

_CALLER = "topic-content-caller"
_KEY = "bbs-topic-content-framework-signing-key"
_NOW = datetime(2026, 10, 28, 8, 0, tzinfo=timezone.utc)


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal() -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 3600,
            "principals": [
                {
                    "type": "user",
                    "subject": {
                        "id": _CALLER,
                        "username": "topic-content@example.test",
                    },
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


def _boot_verifier(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _topic_record() -> ForumTopicRecord:
    return ForumTopicRecord(
        topic_id="topic_endpoint_1",
        author_type="HUMAN",
        author_id=_CALLER,
        title="Edited topic",
        body="Edited description",
        status="OPEN",
        analysis=ForumTopicAnalysisRecord(),
        created_at=_NOW,
        updated_at=_NOW,
    )


def _seed_update(world):
    def update_topic(self, **_kwargs):
        return _topic_record()

    bind_overrides(world, ForumServiceProtocol, {"update_topic": update_topic})


def _seed_delete(world):
    def delete_topic(self, **_kwargs):
        return True

    bind_overrides(world, ForumServiceProtocol, {"delete_topic": delete_topic})


def _seed_post_location(world):
    def get_post_location(self, **kwargs):
        return ForumPostLocationRecord(
            post_id="post_endpoint_1",
            root_post_id="post_endpoint_1",
            page=2,
        )

    bind_overrides(
        world, ForumServiceProtocol, {"get_post_location": get_post_location}
    )


_TOPIC_BODY = {
    "author_type": "HUMAN",
    "author_id": _CALLER,
    "title": "Edited topic",
    "body": "Edited description",
}
_DELETE_BODY = {"author_type": "HUMAN", "author_id": _CALLER}


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


@endpoint_test(
    method="PUT",
    path="/openapi/v1/collaboration/tasks/bbs/topics/{topic_id}",
    scenario="updated",
    input=CaseInput(
        headers={PRINCIPAL_HEADER: _principal()},
        path_params={"topic_id": "topic_endpoint_1"},
        json_body=_TOPIC_BODY,
    ),
    seed=lambda world: (_boot_verifier(world), _seed_update(world)),
    expect=ExpectSuccess(status=200),
)
def public_update_topic_updated():
    """Edit a Topic and return the refreshed Topic detail."""


@endpoint_test(
    method="PUT",
    path="/openapi/v1/collaboration/tasks/bbs/topics/{topic_id}",
    scenario="invalid_body",
    input=CaseInput(
        headers={PRINCIPAL_HEADER: _principal()},
        path_params={"topic_id": "topic_endpoint_1"},
        json_body={"author_type": "HUMAN"},
    ),
    seed=_boot_verifier,
    expect=ExpectError(status=422),
)
def public_update_topic_invalid_body():
    """Reject a Topic edit that omits title and body."""


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/collaboration/tasks/bbs/topics/{topic_id}",
    scenario="deleted",
    input=CaseInput(
        headers={PRINCIPAL_HEADER: _principal()},
        path_params={"topic_id": "topic_endpoint_1"},
        json_body=_DELETE_BODY,
    ),
    seed=lambda world: (_boot_verifier(world), _seed_delete(world)),
    expect=ExpectSuccess(status=200),
)
def public_delete_topic_deleted():
    """Delete a Topic as its declared author."""


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/collaboration/tasks/bbs/topics/{topic_id}",
    scenario="invalid_body",
    input=CaseInput(
        headers={PRINCIPAL_HEADER: _principal()},
        path_params={"topic_id": "topic_endpoint_1"},
        json_body={"author_type": "HUMAN"},
    ),
    seed=_boot_verifier,
    expect=ExpectError(status=422),
)
def public_delete_topic_invalid_body():
    """Reject a Topic deletion that omits the declared author id."""


# ---------------------------------------------------------------------------
# Public post location
# ---------------------------------------------------------------------------


@endpoint_test(
    method="GET",
    path="/openapi/v1/collaboration/tasks/bbs/topics/{topic_id}/posts/{post_id}/location",
    scenario="located",
    input=CaseInput(
        headers={PRINCIPAL_HEADER: _principal()},
        path_params={"topic_id": "topic_endpoint_1", "post_id": "post_endpoint_1"},
        query_params={"page_size": 20},
    ),
    seed=lambda world: (_boot_verifier(world), _seed_post_location(world)),
    expect=ExpectSuccess(status=200),
)
def public_get_post_location_located():
    """Resolve a post to its public display page and floor group."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/collaboration/tasks/bbs/topics/{topic_id}/posts/{post_id}/location",
    scenario="invalid_page_size",
    input=CaseInput(
        headers={PRINCIPAL_HEADER: _principal()},
        path_params={"topic_id": "topic_endpoint_1", "post_id": "post_endpoint_1"},
        query_params={"page_size": 0},
    ),
    seed=_boot_verifier,
    expect=ExpectError(status=422),
)
def public_get_post_location_invalid_page_size():
    """Reject a non-positive reply page size."""


# ---------------------------------------------------------------------------
# Trusted internal mirror surface
# ---------------------------------------------------------------------------


@endpoint_test(
    method="GET",
    path="/api/v1/collaboration/tasks/bbs/topics/{topic_id}/posts/{post_id}/location",
    scenario="located",
    input=CaseInput(
        path_params={"topic_id": "topic_endpoint_1", "post_id": "post_endpoint_1"},
        query_params={"page_size": 20},
    ),
    seed=_seed_post_location,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "data": {
                "post_id": "post_endpoint_1",
                "root_post_id": "post_endpoint_1",
                "page": 2,
            }
        },
    ),
)
def internal_get_post_location_located():
    """Resolve a post location through the trusted internal surface."""


@endpoint_test(
    method="GET",
    path="/api/v1/collaboration/tasks/bbs/topics/{topic_id}/posts/{post_id}/location",
    scenario="invalid_page_size",
    input=CaseInput(
        path_params={"topic_id": "topic_endpoint_1", "post_id": "post_endpoint_1"},
        query_params={"page_size": 0},
    ),
    expect=ExpectError(status=422),
)
def internal_get_post_location_invalid_page_size():
    """Mirror public page-size validation on the trusted internal surface."""


@endpoint_test(
    method="PUT",
    path="/api/v1/collaboration/tasks/bbs/topics/{topic_id}",
    scenario="updated",
    input=CaseInput(
        path_params={"topic_id": "topic_endpoint_1"},
        json_body=_TOPIC_BODY,
    ),
    seed=_seed_update,
    expect=ExpectSuccess(status=200),
)
def internal_update_topic_updated():
    """Mirror Topic editing on the trusted internal surface."""


@endpoint_test(
    method="PUT",
    path="/api/v1/collaboration/tasks/bbs/topics/{topic_id}",
    scenario="invalid_body",
    input=CaseInput(
        path_params={"topic_id": "topic_endpoint_1"},
        json_body={"author_type": "HUMAN"},
    ),
    expect=ExpectError(status=422),
)
def internal_update_topic_invalid_body():
    """Mirror public schema validation for internal Topic edits."""


@endpoint_test(
    method="DELETE",
    path="/api/v1/collaboration/tasks/bbs/topics/{topic_id}",
    scenario="deleted",
    input=CaseInput(
        path_params={"topic_id": "topic_endpoint_1"},
        json_body=_DELETE_BODY,
    ),
    seed=_seed_delete,
    expect=ExpectSuccess(status=200),
)
def internal_delete_topic_deleted():
    """Mirror Topic deletion on the trusted internal surface."""


@endpoint_test(
    method="DELETE",
    path="/api/v1/collaboration/tasks/bbs/topics/{topic_id}",
    scenario="invalid_body",
    input=CaseInput(
        path_params={"topic_id": "topic_endpoint_1"},
        json_body={"author_type": "HUMAN"},
    ),
    expect=ExpectError(status=422),
)
def internal_delete_topic_invalid_body():
    """Mirror public schema validation for internal Topic deletion."""
