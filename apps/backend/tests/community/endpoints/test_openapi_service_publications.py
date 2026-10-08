"""Declarative happy/error coverage for the service-Bot lifecycle surface.

The fourteen lifecycle and edit-lock operations sat on
``coverage_baseline.txt`` as frozen debt, under the 2026-08-17 note: the Bot
Workshop inventory added them, "they require the same gateway-signed principal
that the endpoint case runner still cannot mint", so a case could assert
nothing but a 401.

That reason has expired. ``test_openapi_session_files.py`` mints a principal
here in the test tree — point ``init_principal_verifier_config`` at a local
signing key and hand the runner a token signed with it — and
``test_openapi_routines.py`` / ``test_openapi_resources.py`` already retired
their rows the same way. This file does it for the lifecycle group.

What these cases add over
``tests/community/adapters/http/openapi_v1/test_service_publication_endpoints.py``
— which covers every handler body — is the part a direct ``await handler(...)``
structurally cannot reach. That suite passes the facade positionally, so
FastAPI's dependency wiring never runs: not ``require_user_id``, not
``resolve_owner_id``, not the injector that has to produce
``ServicePublicationFacadeProtocol``, not the router mount, not the 202 the
action routes declare. Here all of them run.

The whole facade is substituted through the injector rather than seeded,
because every operation on this surface is an orchestration over the publish
repository, the flow/approval services and the collaborator lock — driving a
draft into ``prestable`` for real would be a publish-pipeline test, and there
is one. What is under test here is the wire: statuses, bodies, envelopes and
the DI graph behind them.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.service_publication_facade import (
    ServicePublicationFacadeProtocol,
)
from agentclaw.community.api.service_edit_lock_service import (
    ServiceEditLockServiceProtocol,
)
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    bind_overrides,
    endpoint_test,
)

_OWNER = "lifecycle-owner"
_BOT_ID = "lifecycle-bot"
_KEY = "service-publications-framework-signing-key-32b"
_LIFECYCLE = "/openapi/v1/bots/{bot_id}/lifecycle"
_LIFECYCLE_UPGRADE = f"{_LIFECYCLE}/{{publication_id}}/upgrade"
_EDIT_LOCK = "/openapi/v1/bots/{bot_id}/edit-lock"
_PATH_PARAMS = {"bot_id": _BOT_ID}
#: Timestamps as a string, the shape the facade's own projection emits and the
#: shape ``ServicePublication`` parses — no local clock, nothing to drift.
_NOW = "2026-08-24T12:00:00Z"
_PUBLICATION_ID = 7
_CARD_ID = f"service:{_BOT_ID}:{_PUBLICATION_ID}"


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
                    "subject": {"id": _OWNER, "username": "lifecycle@example.test"},
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}
_QUERY = {"user_id": _OWNER}
_FORBIDDEN_QUERY = {"user_id": "another-user"}


def _publication(bot_id: str) -> dict:
    """One version card exactly as the facade projects it."""
    return {
        "bot_id": bot_id,
        "publication_id": _PUBLICATION_ID,
        "card_id": f"service:{bot_id}:{_PUBLICATION_ID}",
        "version": _PUBLICATION_ID,
        "status": "draft",
        "internal_status": "draft",
        "live_version": None,
        "deployment": None,
        "approval": None,
        "available_actions": ["publish_staging", "delete"],
        "created_at": _NOW,
        "updated_at": _NOW,
    }


def _operation(bot_id: str, action: str) -> dict:
    """The acknowledgement every asynchronous lifecycle command answers with."""
    return {
        "bot_id": bot_id,
        "publication_id": _PUBLICATION_ID,
        "action": action,
        "accepted": True,
        "operation_status": "pending",
        "approval": None,
    }


def _lock_info(holder: str):
    """What ``get_lock`` returns: the lock plus its surrounding context.

    ``router._lock_payload`` reads these attributes off it, so the stand-in has
    to carry the same shape the real ``_service_lock_info`` builds. ``holder``
    is the ``actor_id`` the handler passed in, which is what makes
    ``holder_user_id`` in the response evidence that ``require_user_id``'s
    answer reached the facade.
    """
    return SimpleNamespace(
        lock=SimpleNamespace(holder_user_id=holder),
        holder_name="Lifecycle Owner",
        has_collaborators=True,
        is_owner=True,
        need_lock=True,
    )


def _seed_verifier(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _seed_happy_services(world) -> None:
    _seed_verifier(world)
    make_bot(world, bot_id=_BOT_ID, owner_id=_OWNER)

    # Each stand-in answers *from its arguments* rather than from constants, so
    # the assertions downstream are on values that travelled the whole way:
    # ``bot_id`` off the path, ``actor_id`` out of ``require_user_id``, ``stage``
    # and ``should_approval`` out of the parsed body. A handler that dropped or
    # transposed one would change the response, not merely fail to.

    def list_publications(_self, bot_id, **_kwargs):
        return {"bot_id": bot_id, "items": [_publication(bot_id)]}

    def convert_to_service(_self, bot_id, **_kwargs):
        return _publication(bot_id)

    def upgrade_publication(_self, bot_id, publication_id, **_kwargs):
        result = _publication(bot_id)
        result["publication_id"] = publication_id
        result["card_id"] = f"service:{bot_id}:{publication_id}"
        return result

    def get_service_config(_self, bot_id, **_kwargs):
        return {"bot_id": bot_id, "should_approval": False}

    def update_service_config(_self, bot_id, *, should_approval, **_kwargs):
        return {"bot_id": bot_id, "should_approval": should_approval}

    async def advance(_self, bot_id, stage, **_kwargs):
        action = "publish_online" if stage == "online" else "publish_staging"
        return _operation(bot_id, action)

    def restart(_self, bot_id, _stage, **_kwargs):
        return _operation(bot_id, "restart_publish")

    async def cancel_staging(_self, bot_id, **_kwargs):
        return _operation(bot_id, "cancel_staging")

    async def offline(_self, bot_id, **_kwargs):
        return _operation(bot_id, "offline")

    async def retry(_self, bot_id, **_kwargs):
        return _operation(bot_id, "retry")

    def delete_initial_draft(_self, _bot_id, **_kwargs) -> bool:
        return True

    bind_overrides(
        world,
        ServicePublicationFacadeProtocol,
        {
            "list_publications": list_publications,
            "convert_to_service": convert_to_service,
            "upgrade_publication": upgrade_publication,
            "get_service_config": get_service_config,
            "update_service_config": update_service_config,
            "advance": advance,
            "restart": restart,
            "cancel_staging": cancel_staging,
            "offline": offline,
            "retry": retry,
            "delete_initial_draft": delete_initial_draft,
        },
    )


def _seed_happy_edit_locks(world) -> None:
    """Wire edit-locks alone; resolving the publication facade is a failure."""
    _seed_verifier(world)
    make_bot(world, bot_id=_BOT_ID, owner_id=_OWNER)

    def get_lock(_self, _bot_id, *, actor_id, **_kwargs):
        return _lock_info(actor_id)

    def acquire_lock(_self, _bot_id, *, actor_id, **_kwargs):
        return _lock_info(actor_id).lock

    def release_lock(_self, _bot_id, **_kwargs) -> bool:
        return True

    def steal_lock(_self, _bot_id, *, actor_id, **_kwargs):
        return _lock_info(actor_id).lock

    bind_overrides(
        world,
        ServiceEditLockServiceProtocol,
        {
            "get_lock": get_lock,
            "acquire_lock": acquire_lock,
            "release_lock": release_lock,
            "steal_lock": steal_lock,
        },
    )


_ADVANCE_BODY = {"stage": "prestable"}
_RESTART_BODY = {"stage": "online"}
_APPROVAL_BODY = {"should_approval": True}


def _case(**extra) -> CaseInput:
    return CaseInput(
        path_params=_PATH_PARAMS, query_params=_QUERY, headers=_HEADERS, **extra
    )


#: ``(method, path, input, success status, response fragment)``.
#:
#: The action routes answer **202** — they acknowledge a command the pipeline
#: finishes later — and that is read off ``_action_route``'s
#: ``status_code=202``, not assumed. The fragment pins the envelope *and* the
#: part of the payload that could only have come from this operation: the
#: ``action`` each command names, the ``should_approval`` a PUT body carried in,
#: the ``acquired`` flag only acquire/steal set. A case that merely asserted the
#: status would pass for any handler mounted at the path.
_HAPPY_CASES = (
    (
        "GET",
        _LIFECYCLE,
        _case(),
        200,
        {"code": 200000, "data": {"bot_id": _BOT_ID, "items": [{"card_id": _CARD_ID}]}},
    ),
    ("DELETE", _LIFECYCLE, _case(), 200, {"code": 200000, "data": {"deleted": True}}),
    (
        "POST",
        f"{_LIFECYCLE}/upgrade",
        _case(),
        200,
        {"code": 200000, "data": {"card_id": _CARD_ID, "status": "draft"}},
    ),
    (
        "POST",
        _LIFECYCLE_UPGRADE,
        CaseInput(
            path_params={**_PATH_PARAMS, "publication_id": _PUBLICATION_ID},
            query_params=_QUERY,
            headers=_HEADERS,
        ),
        200,
        {"code": 200000, "data": {"card_id": _CARD_ID, "status": "draft"}},
    ),
    (
        "GET",
        f"{_LIFECYCLE}/approval",
        _case(),
        200,
        {"code": 200000, "data": {"bot_id": _BOT_ID, "should_approval": False}},
    ),
    (
        "PUT",
        f"{_LIFECYCLE}/approval",
        _case(json_body=_APPROVAL_BODY),
        200,
        # ``True`` here is the request body arriving: the stand-in echoes what
        # the handler passed it, and the seeded state reads ``False``.
        {"code": 200000, "data": {"bot_id": _BOT_ID, "should_approval": True}},
    ),
    (
        "POST",
        f"{_LIFECYCLE}/advance",
        _case(json_body=_ADVANCE_BODY),
        202,
        {"code": 202000, "data": {"action": "publish_staging", "accepted": True}},
    ),
    (
        "POST",
        f"{_LIFECYCLE}/restart",
        _case(json_body=_RESTART_BODY),
        202,
        {"code": 202000, "data": {"action": "restart_publish"}},
    ),
    (
        "POST",
        f"{_LIFECYCLE}/cancel-staging",
        _case(),
        202,
        {"code": 202000, "data": {"action": "cancel_staging"}},
    ),
    (
        "POST",
        f"{_LIFECYCLE}/offline",
        _case(),
        202,
        {"code": 202000, "data": {"action": "offline"}},
    ),
    (
        "POST",
        f"{_LIFECYCLE}/retry",
        _case(),
        202,
        {"code": 202000, "data": {"action": "retry"}},
    ),
    (
        "GET",
        _EDIT_LOCK,
        _case(),
        200,
        # A read reports the lock but claims no acquisition of its own.
        {
            "code": 200000,
            "data": {"locked": True, "holder_user_id": _OWNER, "acquired": None},
        },
    ),
    (
        "POST",
        _EDIT_LOCK,
        _case(),
        200,
        {"code": 200000, "data": {"locked": True, "acquired": True}},
    ),
    ("DELETE", _EDIT_LOCK, _case(), 200, {"code": 200000, "data": {"released": True}}),
    (
        "POST",
        f"{_EDIT_LOCK}/steal",
        _case(),
        200,
        {
            "code": 200000,
            "data": {"locked": True, "acquired": True, "holder_user_id": _OWNER},
        },
    ),
)


for _method, _path, _input, _status, _contains in _HAPPY_CASES:
    _seed = (
        _seed_happy_edit_locks
        if _path.startswith(_EDIT_LOCK)
        else _seed_happy_services
    )
    endpoint_test(
        method=_method,
        path=_path,
        scenario="happy",
        input=_input,
        seed=_seed,
        expect=ExpectSuccess(status=_status, json_contains=_contains),
    )(lambda: None)


# The refusal every user-scoped operation on this surface owes: ``user_id``
# names someone other than the caller the principal authenticated. It is raised
# by ``require_user_id`` ahead of the handler, so no facade is substituted —
# reaching one would itself be the bug.
for _method, _path, _input, _status, _contains in _HAPPY_CASES:
    endpoint_test(
        method=_method,
        path=_path,
        scenario="forbidden_user_scope",
        input=CaseInput(
            path_params=_input.path_params,
            query_params=_FORBIDDEN_QUERY,
            headers=_HEADERS,
            json_body=_input.json_body,
        ),
        seed=_seed_verifier,
        expect=ExpectError(status=403, json_contains={"data": None}),
    )(lambda: None)
