"""Declarative happy/error coverage for the public engine-runtime surface.

Ten operations across four routers — approvals, connection, engine and
models — sat on ``coverage_baseline.txt`` as frozen debt for the reason the
sibling files record: the case runner authenticates with ``x-user-id``,
``require_principal`` accepts only a gateway-signed token, and the harness had
no minter, so a case could assert nothing but a 401.

``test_openapi_session_files.py`` retired that reason by minting a principal
inside the test tree — ``init_principal_verifier_config`` is pointed at a local
signing key and the runner is handed a token signed with it. This file follows
it, and follows ``test_openapi_routines.py`` / ``test_openapi_resources.py``
for the case-table shape.

What these add over ``tests/community/adapters/http/openapi_v1/engine_runtime/``
— which covers every handler thoroughly — is the part a direct ``await
handler(...)`` structurally cannot reach: the assembled application. Those
suites build a bespoke ``FastAPI()`` hosting one router with a hand-bound
injector; here the real app is assembled, so the DI graph, the router mounts,
the ``Check(MEMBER)`` authorization seam, path/query binding (including the
``{model_id:path}`` convertor) and the envelope are all on the wire.

Two seams are stood in for, both at their Protocol boundary and both because
the operation's whole purpose is to reach a device this test has none of:

* ``EngineRuntimeRelayProtocol`` — ``resolve_bot_off_loop`` answers the bot
  facts the gate reads, and ``call`` answers each engine route by path, in the
  raw shape the real engine returns (the wrapped ``{"models": …}`` payload, the
  un-enveloped status payload, the ``fallback`` map) so the handlers' mapping
  is what is exercised rather than a pre-shaped fixture.
* ``EngineConnectionServiceProtocol`` — the connection endpoint composes its
  socket inside the service, which resolves a device binding.
"""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.engine_connection_service import (
    EngineConnectionServiceProtocol,
)
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.core.engine_runtime.models import (
    BotFacts,
    ConnectionResult,
    EngineResult,
    SocketInfo,
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

_OWNER = "engine-runtime-owner"
_BOT_ID = "engine-runtime-bot"
_SESSION_KEY = "session:2d20edc1:user:165137"
#: Provider-qualified on purpose: the id spans a slash, which is the whole
#: reason ``get_model`` declares ``{model_id:path}``. A single-segment id would
#: pass through a plain ``{model_id}`` route just as well and prove nothing.
_MODEL_ID = "openai/gpt-5.3"
_KEY = "engine-runtime-framework-signing-key-at-least-32-bytes"
_BOTS = "/openapi/v1/bots/{bot_id}"
_PATH_PARAMS = {"bot_id": _BOT_ID}
_MODEL_PATH_PARAMS = {**_PATH_PARAMS, "model_id": _MODEL_ID}


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
                        "id": _OWNER,
                        "username": "engine-runtime@example.test",
                    },
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}


def _query(**extra) -> dict:
    return {"user_id": _OWNER, **extra}


def _forbidden_query(**extra) -> dict:
    return {"user_id": "another-user", **extra}


#: What each relayed engine route answers, keyed by the path the handler
#: forwards to. Written in the engine's own shapes — see the module docstring.
_ENGINE_PAYLOADS: dict[str, object] = {
    "/api/approvals/mode/get": {"sessionKey": _SESSION_KEY, "mode": "on-miss"},
    # ``ok: True``: the write reports application separately from the call, and
    # ``_reject_refused_set`` raises on ``ok is False``.
    "/api/approvals/mode/set": {
        "sessionKey": _SESSION_KEY,
        "mode": "never",
        "ok": True,
    },
    # ``approval.set`` must be declared or ``list_approval_modes`` answers 501:
    # that route publishes the *write's* accept-set, so the write's capability
    # is what decides whether it has anything to say.
    "/api/engine/capabilities": {
        "supported": ["approval.set", "sessions.list"],
        "limited": {"mcp.install": "通过 mcporter 命令启动"},
        "fallback": {"terminal.exec": "internal note"},
    },
    # Relayed with ``enveloped=False`` — the one engine route that answers its
    # payload raw, with ``running`` nested under ``process``.
    "/api/engine/status": {
        "engine": "openclaw",
        "active_connections": 1,
        "process": {"running": True, "pid": 4211},
    },
    "/api/engine/list": [
        {"name": "openclaw", "version": "1.2.3", "active": True},
        {"name": "claude_code", "version": "0.9.0", "active": False},
    ],
    "/api/engine/restart": {"status": "restarting"},
    # The engine wraps its model listing rather than answering a bare list.
    "/api/models": {
        "models": [{"id": _MODEL_ID, "name": "GPT-5.3", "provider": "openai"}],
        "total": 1,
    },
    f"/api/models/{_MODEL_ID}": {
        "id": _MODEL_ID,
        "name": "GPT-5.3",
        "provider": "openai",
    },
}


def _seed_verifier(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _seed_happy_services(world) -> None:
    _seed_verifier(world)
    # Every row here carries ``Check(MEMBER)`` (the connection endpoint,
    # ``ServiceChecked``, resolves the same bot inside its service), so
    # ``bot_access`` resolves ``(bot_id, owner_id)`` against the real
    # ``BotRepository`` before the handler runs. The relay stand-in below
    # answers without a Bot row; the seam does not, and refuses.
    make_bot(world, bot_id=_BOT_ID, owner_id=_OWNER, active_engine="openclaw")

    async def resolve_bot_off_loop(_self, bot_id, owner_id, _caller_id):
        # ``personal``: an operable type whose only runtime is the draft, which
        # is the stage every case here addresses by default.
        return BotFacts(
            bot_id=bot_id,
            bot_type="personal",
            active_engine="openclaw",
            owner_id=owner_id,
        )

    async def call(_self, *, path, **_kwargs):
        return EngineResult(data=_ENGINE_PAYLOADS[path])

    bind_overrides(
        world,
        EngineRuntimeRelayProtocol,
        {
            "resolve_bot_off_loop": resolve_bot_off_loop,
            "call": call,
        },
    )

    def build(_self, *, bot_id, owner_id, caller_id, stage):
        return ConnectionResult(
            engine="openclaw",
            expires_at="2026-07-30T14:30:00+00:00",
            sockets=[
                SocketInfo(
                    kind="chat",
                    url=(
                        "wss://gw.example/openapi/v1/bots/messages/ws/tgt"
                        "/api/openclaw/ws?x-proxypass-token=tok"
                    ),
                )
            ],
        )

    bind_overrides(world, EngineConnectionServiceProtocol, {"build": build})


_MODE_BODY = {"mode": "never"}

#: ``(method, path, input, success_status)``. The status is the router's real
#: one: none of these eleven declare ``status_code=``, so all answer 200.
_HAPPY_CASES = (
    (
        "GET",
        f"{_BOTS}/approvals/mode",
        CaseInput(
            path_params=_PATH_PARAMS,
            query_params=_query(session_key=_SESSION_KEY),
            headers=_HEADERS,
        ),
        200,
    ),
    (
        "GET",
        f"{_BOTS}/approvals/modes",
        CaseInput(
            path_params=_PATH_PARAMS, query_params=_query(), headers=_HEADERS
        ),
        200,
    ),
    (
        "PUT",
        f"{_BOTS}/approvals/mode",
        CaseInput(
            path_params=_PATH_PARAMS,
            query_params=_query(session_key=_SESSION_KEY),
            headers=_HEADERS,
            json_body=_MODE_BODY,
        ),
        200,
    ),
    (
        "GET",
        f"{_BOTS}/connection",
        CaseInput(
            path_params=_PATH_PARAMS, query_params=_query(), headers=_HEADERS
        ),
        200,
    ),
    (
        "GET",
        f"{_BOTS}/engine/available",
        CaseInput(
            path_params=_PATH_PARAMS, query_params=_query(), headers=_HEADERS
        ),
        200,
    ),
    (
        "GET",
        f"{_BOTS}/engine/capabilities",
        CaseInput(
            path_params=_PATH_PARAMS, query_params=_query(), headers=_HEADERS
        ),
        200,
    ),
    (
        "GET",
        f"{_BOTS}/engine/status",
        CaseInput(
            path_params=_PATH_PARAMS, query_params=_query(), headers=_HEADERS
        ),
        200,
    ),
    (
        "POST",
        f"{_BOTS}/engine/restart",
        CaseInput(
            path_params=_PATH_PARAMS, query_params=_query(), headers=_HEADERS
        ),
        200,
    ),
    (
        "GET",
        f"{_BOTS}/models",
        CaseInput(
            path_params=_PATH_PARAMS, query_params=_query(), headers=_HEADERS
        ),
        200,
    ),
    (
        "GET",
        f"{_BOTS}/models/{{model_id:path}}",
        CaseInput(
            path_params=_MODEL_PATH_PARAMS,
            query_params=_query(),
            headers=_HEADERS,
        ),
        200,
    ),
)


#: What each operation must actually have mapped, beyond answering 200. Keyed
#: by ``(method, path)``; an operation with no entry asserts the status alone.
#: These pin the handler's own projection — the ``process.running`` lift, the
#: ``fallback`` → ``unavailable`` rename, the unwrapped ``{"models": …}`` page —
#: so a route that 200s with an empty body cannot pass for covered.
_HAPPY_BODIES: dict[tuple[str, str], dict] = {
    ("GET", f"{_BOTS}/approvals/mode"): {
        "data": {"session_key": _SESSION_KEY, "mode": "on-miss"}
    },
    ("GET", f"{_BOTS}/approvals/modes"): {
        "data": [{"value": "never"}, {"value": "on-miss"}, {"value": "approve"}]
    },
    ("PUT", f"{_BOTS}/approvals/mode"): {
        "data": {"session_key": _SESSION_KEY, "mode": "never"}
    },
    ("GET", f"{_BOTS}/connection"): {
        "data": {"engine": "openclaw", "sockets": [{"kind": "chat"}]}
    },
    ("GET", f"{_BOTS}/engine/available"): {
        "data": [{"engine": "openclaw", "version": "1.2.3", "active": True}]
    },
    ("GET", f"{_BOTS}/engine/capabilities"): {
        "data": {
            "supported": ["approval.set", "sessions.list"],
            "limited": ["mcp.install"],
            "unavailable": ["terminal.exec"],
        }
    },
    ("GET", f"{_BOTS}/engine/status"): {
        "data": {"engine": "openclaw", "active_connections": 1, "running": True}
    },
    ("POST", f"{_BOTS}/engine/restart"): {
        "data": {"bot_id": _BOT_ID, "status": "restarting"}
    },
    ("GET", f"{_BOTS}/models"): {
        "data": {"total": 1, "items": [{"model_id": _MODEL_ID, "provider": "openai"}]}
    },
    ("GET", f"{_BOTS}/models/{{model_id:path}}"): {
        "data": {"model_id": _MODEL_ID, "name": "GPT-5.3", "provider": "openai"}
    },
}


for _method, _path, _input, _status in _HAPPY_CASES:
    endpoint_test(
        method=_method,
        path=_path,
        scenario="happy",
        input=_input,
        seed=_seed_happy_services,
        expect=ExpectSuccess(
            status=_status,
            json_contains=_HAPPY_BODIES.get((_method, _path), {}),
        ),
    )(lambda: None)


# The refusal every user-scoped operation on this surface owes: ``user_id``
# names someone other than the caller the principal authenticated. It is raised
# by ``require_user_id`` ahead of the handler, so neither the relay nor the
# connection service is seeded — reaching either would itself be the bug.
for _method, _path, _input, _status in _HAPPY_CASES:
    endpoint_test(
        method=_method,
        path=_path,
        scenario="forbidden_user_scope",
        input=CaseInput(
            path_params=_input.path_params,
            query_params=_forbidden_query(
                **{
                    k: v
                    for k, v in _input.query_params.items()
                    if k != "user_id"
                }
            ),
            headers=_HEADERS,
            json_body=_input.json_body,
        ),
        seed=_seed_verifier,
        expect=ExpectError(status=403, json_contains={"data": None}),
    )(lambda: None)
