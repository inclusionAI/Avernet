"""Endpoint coverage for the internal and public BBS helpful-marker routes."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.core.task.forum.models import ForumPostRecord
from agentclaw.community.core.task.forum.service_protocol import ForumServiceProtocol
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

_INTERNAL_PATH = (
    "/api/v1/collaboration/tasks/bbs/topics/"
    "{topic_id}/posts/{post_id}/helpful"
)
_PUBLIC_PATH = (
    "/openapi/v1/collaboration/tasks/bbs/topics/"
    "{topic_id}/posts/{post_id}/helpful"
)
_CALLER = "bbs-helpful-author"
_KEY = "bbs-helpful-framework-signing-key"
_BODY = {"author_type": "HUMAN", "author_id": _CALLER, "helpful": True}


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _boot_verifier(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


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
                        "username": "bbs-helpful@example.test",
                    },
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


def _headers() -> dict[str, str]:
    return {PRINCIPAL_HEADER: _principal()}


def _seed_helpful_service(world) -> None:
    def set_post_helpful(
        _self,
        *,
        topic_id: str,
        post_id: str,
        author_type: str,
        author_id: str,
        helpful: bool,
    ) -> ForumPostRecord:
        return ForumPostRecord(
            post_id=post_id,
            topic_id=topic_id,
            author_type=author_type,
            author_id=author_id,
            body="A helpful reply",
            created_at=datetime(2026, 10, 27, 8, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 10, 27, 8, 1, tzinfo=timezone.utc),
            helpful=helpful,
        )

    bind_overrides(
        world,
        ForumServiceProtocol,
        {"set_post_helpful": set_post_helpful},
    )


def _seed_public_helpful(world) -> None:
    _boot_verifier(world)
    _seed_helpful_service(world)


_HAPPY_INPUT = CaseInput(
    path_params={"topic_id": "topic-helpful", "post_id": "post-helpful"},
    json_body=_BODY,
)


@endpoint_test(
    method="PUT",
    path=_INTERNAL_PATH,
    scenario="helpful_marked",
    input=_HAPPY_INPUT,
    seed=_seed_helpful_service,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"post_id": "post-helpful", "helpful": True},
        },
    ),
)
def internal_helpful_marked():
    """The trusted internal surface forwards the declared author and state."""


@endpoint_test(
    method="PUT",
    path=_INTERNAL_PATH,
    scenario="invalid_body",
    input=CaseInput(
        path_params={"topic_id": "topic-helpful", "post_id": "post-helpful"},
        json_body={"helpful": True},
    ),
    expect=ExpectError(status=422),
)
def internal_helpful_invalid_body():
    """The internal surface rejects a marker update without an author."""


@endpoint_test(
    method="PUT",
    path=_PUBLIC_PATH,
    scenario="authenticated_helpful_marked",
    input=CaseInput(
        path_params=_HAPPY_INPUT.path_params,
        headers=_headers(),
        json_body=_BODY,
    ),
    seed=_seed_public_helpful,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"post_id": "post-helpful", "helpful": True},
        },
    ),
)
def public_helpful_marked():
    """A verified public caller can submit the author declared in the body."""


@endpoint_test(
    method="PUT",
    path=_PUBLIC_PATH,
    scenario="unauthenticated",
    input=_HAPPY_INPUT,
    seed=_boot_verifier,
    expect=ExpectError(
        status=401,
        json_contains={"code": 401000, "message": "Unauthorized"},
    ),
)
def public_helpful_unauthenticated():
    """The public surface still requires a verified principal."""
