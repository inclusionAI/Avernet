"""``?stage=`` on the five per-bot file operations, at the HTTP boundary.

What the services do with a stage is pinned in
``tests/community/core/services/``; this is about the wire — that the parameter
reaches the service unchanged, that the default is the draft, that a write to a
published runtime is refused with the fixed 409, and that the retiring twins
never gained the parameter.
"""

from __future__ import annotations

from unittest.mock import MagicMock, create_autospec

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.openapi_v1.bots.engine_config import (
    router as engine_config_router,
)
from agentclaw.community.adapters.http.openapi_v1.deprecated import (
    ENGINE_RUNTIME_GROUPS as _LEGACY_ENGINE_RUNTIME,
    GRANT_CHECKED_GROUPS as _LEGACY_GRANT_CHECKED,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.identity import (
    router as identity_router,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.engine_config_service import EngineConfigServiceProtocol
from agentclaw.community.core.engine_runtime.errors import (
    EngineStageNotLiveError,
    EngineStageReadOnlyError,
)
from agentclaw.community.core.services.identity import IdentityService
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    user_scoped_client,
)

BOT = "b1"
USER = "u1"

_ENGINE_CONFIG = f"/openapi/v1/bots/{BOT}/engine/config"
_IDENTITY_LIST = f"/openapi/v1/bots/{BOT}/identity"
_IDENTITY_FILE = f"/openapi/v1/bots/{BOT}/identity/RULES"

#: The addresses #1074 retired. Frozen: they must not have grown ``stage``.
_LEGACY_ENGINE_CONFIG = f"/openapi/v1/bots/{BOT}/engine-config"
_LEGACY_IDENTITY_LIST = f"/openapi/v1/bots/identity/{BOT}"
_LEGACY_IDENTITY_FILE = f"/openapi/v1/bots/identity/{BOT}/RULES"


@pytest.fixture
def bot_service():
    m = MagicMock()
    m.get_bot.return_value = {
        "id": 100,
        "bot_id": BOT,
        "owner_id": USER,
        "entity_id": USER,
        "entity_type": "staff",
        "bot_type": "service",
        "active_engine": "openclaw",
    }
    return m


@pytest.fixture
def engine_config():
    # ``create_autospec`` rather than a bare AsyncMock: it binds the real
    # signatures, so an adapter forwarding a keyword the service does not accept
    # fails here instead of at the first production request. A ``spec=`` alone
    # would only restrict attribute *names*.
    m = create_autospec(EngineConfigServiceProtocol, instance=True)
    m.read_bot_config.return_value = {"k": "v"}
    m.write_bot_config.return_value = None
    return m


@pytest.fixture
def identity():
    # Autospecced for the reason above. The return values are set on the
    # autospecced children rather than replacing them, which would discard the
    # bound signature and the check with it.
    m = create_autospec(IdentityService, instance=True)
    m.list_bot_files.return_value = [("RULES.md", True)]
    m.get_bot_file.return_value = MagicMock(
        content="body", file_path="identity/RULES.md"
    )
    m.update_bot_file.return_value = MagicMock(file_path="identity/RULES.md")
    return m


@pytest.fixture
def client(bot_service, engine_config, identity):
    class _M(Module):
        def configure(self, binder):
            binder.bind(BotServiceProtocol, to=bot_service)
            binder.bind(EngineConfigServiceProtocol, to=engine_config)
            binder.bind(IdentityService, to=identity)

    app = FastAPI()
    app.include_router(engine_config_router)
    app.include_router(identity_router)
    for legacy in (*_LEGACY_ENGINE_RUNTIME, *_LEGACY_GRANT_CHECKED):
        app.include_router(legacy)
    app.dependency_overrides[require_principal] = lambda: {"user_id": USER}
    attach_injector(app, Injector([_M()]))
    mount_public_error_handlers(app)
    return user_scoped_client(app, USER)


# ── the parameter reaches the service ────────────────────────────────────────


@pytest.mark.parametrize("stage", ["draft", "verify", "online"])
def test_each_read_forwards_the_named_stage(client, engine_config, identity, stage):
    assert client.get(_ENGINE_CONFIG, params={"stage": stage}).status_code == 200
    assert engine_config.read_bot_config.call_args.kwargs["stage"] == stage

    assert client.get(_IDENTITY_LIST, params={"stage": stage}).status_code == 200
    assert identity.list_bot_files.call_args.kwargs["stage"] == stage

    assert client.get(_IDENTITY_FILE, params={"stage": stage}).status_code == 200
    assert identity.get_bot_file.call_args.kwargs["stage"] == stage


def test_naming_no_stage_addresses_the_draft(client, engine_config, identity):
    """The compatibility pin: an unchanged request behaves as it always did."""
    client.get(_ENGINE_CONFIG)
    assert engine_config.read_bot_config.call_args.kwargs["stage"] == "draft"

    client.get(_IDENTITY_LIST)
    assert identity.list_bot_files.call_args.kwargs["stage"] == "draft"

    client.get(_IDENTITY_FILE)
    assert identity.get_bot_file.call_args.kwargs["stage"] == "draft"

    client.put(_ENGINE_CONFIG, json={"a": 1})
    assert engine_config.write_bot_config.call_args.kwargs["stage"] == "draft"

    client.put(_IDENTITY_FILE, json={"content": "x"})
    assert identity.update_bot_file.call_args.kwargs["stage"] == "draft"


def test_a_stage_outside_the_enum_never_reaches_a_handler(client, engine_config):
    resp = client.get(_ENGINE_CONFIG, params={"stage": "eval"})
    assert resp.status_code == 422
    engine_config.read_bot_config.assert_not_called()


# ── refusals ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("stage", ["verify", "online"])
def test_a_published_write_is_refused_with_the_read_only_409(
    client, engine_config, identity, stage
):
    engine_config.write_bot_config.side_effect = EngineStageReadOnlyError("no")
    identity.update_bot_file.side_effect = EngineStageReadOnlyError("no")

    for url, body in (
        (_ENGINE_CONFIG, {"a": 1}),
        (_IDENTITY_FILE, {"content": "x"}),
    ):
        resp = client.put(url, params={"stage": stage}, json=body)
        assert resp.status_code == 409, url
        assert resp.json()["message"] == "The requested stage is read-only"


def test_a_stage_with_no_live_runtime_keeps_its_own_409(client, engine_config):
    engine_config.read_bot_config.side_effect = EngineStageNotLiveError("no")

    resp = client.get(_ENGINE_CONFIG, params={"stage": "online"})
    assert resp.status_code == 409
    assert resp.json()["message"] == "No live runtime at the requested stage"


# ── the retiring twins ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url", [_LEGACY_ENGINE_CONFIG, _LEGACY_IDENTITY_LIST, _LEGACY_IDENTITY_FILE]
)
def test_the_retiring_addresses_still_read_the_draft(
    client, engine_config, identity, url
):
    """Frozen, and not merely undocumented.

    A caller who sends ``?stage=online`` at a retiring address gets the draft:
    the parameter is not declared there, so FastAPI ignores it. Registering the
    legacy route re-uses the *same endpoint function*, so without the
    ``without_parameter`` transform this would forward ``online`` instead.
    """
    resp = client.get(url, params={"stage": "online"})
    assert resp.status_code == 200

    forwarded = [
        m.call_args.kwargs.get("stage")
        for m in (
            engine_config.read_bot_config,
            identity.list_bot_files,
            identity.get_bot_file,
        )
        if m.call_args is not None
    ]
    assert forwarded and set(forwarded) == {"draft"}


@pytest.mark.parametrize(
    ("url", "body"),
    [
        (_LEGACY_ENGINE_CONFIG, {"a": 1}),
        (_LEGACY_IDENTITY_FILE, {"content": "x"}),
    ],
)
def test_a_retiring_write_naming_a_published_stage_still_writes_the_draft(
    client, engine_config, identity, url, body
):
    """Pinned because it is the one place "nothing is written" does not hold.

    The current addresses refuse a published write with a 409. These do not:
    they never declared the parameter, so it is ignored and the draft is
    written — the contract they were frozen with. That is a deliberate
    consequence of freezing them rather than an oversight, and it is the
    strongest reason to migrate, so it is asserted rather than left to be
    discovered.
    """
    assert client.put(url, params={"stage": "online"}, json=body).status_code == 200

    written = [
        m.call_args.kwargs.get("stage")
        for m in (engine_config.write_bot_config, identity.update_bot_file)
        if m.call_args is not None
    ]
    assert written == ["draft"]
