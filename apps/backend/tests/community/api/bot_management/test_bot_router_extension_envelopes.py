"""Lifecycle-extension rejections map to controlled route envelopes.

The create and delete routes translate the bound extension's
rejections: invalid input → 400, conflict → 409, blocked → 500
carrying the extension's ``data`` payload. The extension enters the
route the production way — through the attached injector's
``BotCreateExtension`` binding.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
)
from agentclaw.community.plugin_api.bot_create_extension import (
    BotCreateExtension,
    ExtensionBlocked,
    ExtensionConflict,
    ExtensionInvalidInput,
)
from tests.community.api.bot_management._bot_router_test_support import (  # noqa: F401
    _bind_bot_service,
    client,
    mock_bot_service,
    mock_passport,
)

CREATE_URL = "/api/bots"
DELETE_URL_TPL = "/api/bots/{bot_id}"


class ThrowingExtension:
    """Extension double: raises the configured rejection at each hook."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def resolve_create(self, *, bot_id, user_id, request):
        raise self._exc

    def after_bot_registered(self, *, bot_id, user_id):
        pass

    def before_delete(self, *, bot_id, owner_id):
        raise self._exc

    def on_delete_completed(self, *, bot_id, owner_id):
        pass

    def on_delete_not_found(self, *, bot_id, owner_id):
        pass


def _poison_with(tc, svc, passport, exc):
    """Layer the throwing extension's binding over the fixture's Noop."""

    class _Poison(Module):
        def configure(self, binder):
            binder.bind(BotCreateExtension, to=ThrowingExtension(exc))

    attach_injector(tc.app, Injector([_bind_bot_service(
        svc, bot_repo=MagicMock(), passport=passport,
        auth=MagicMock(), auth_rel=MagicMock(), skill_set_factory=MagicMock(),
    ), _Poison()]))
    return tc


_REJECTIONS = [
    pytest.param(
        ExtensionInvalidInput("相关请求不能同时携带模板字段"),
        400, None, id="invalid-input-400",
    ),
    pytest.param(
        ExtensionConflict("该应用已存在同角色的关联实例"),
        409, None, id="conflict-409",
    ),
    pytest.param(
        ExtensionBlocked("关联实例状态暂不可达", data={"bot_id": "b1"}),
        500, {"bot_id": "b1"}, id="blocked-500-with-data",
    ),
]


class TestCreateRouteEnvelope:
    @pytest.mark.parametrize(("exc", "code", "data"), _REJECTIONS)
    def test_extension_rejections_map_to_envelopes(
        self, client, mock_passport, exc, code, data
    ):
        tc, svc, passport = client
        _poison_with(tc, svc, passport, exc)
        resp = tc.post(CREATE_URL, json={"bot_name": "NewBot", "engine_type": "openclaw"})
        body = resp.json()
        assert body["success"] is False
        assert body["error_code"] == code
        assert body["data"] == data


class TestDeleteRouteEnvelope:
    @pytest.mark.parametrize(("exc", "code", "data"), _REJECTIONS)
    def test_extension_rejections_map_to_envelopes(
        self, client, mock_passport, exc, code, data
    ):
        tc, svc, passport = client
        _poison_with(tc, svc, passport, exc)
        resp = tc.delete(DELETE_URL_TPL.format(bot_id="bot-1"))
        body = resp.json()
        assert body["success"] is False
        assert body["error_code"] == code
        assert body["data"] == data


class NotFoundThrowingExtension:
    """Extension double: healthy before_delete, raises at the not-found
    cleanup hook — the callback that runs inside the route's own
    ``except BotNotFoundError`` handler."""

    def __init__(self, exc: Exception | None = None) -> None:
        self._exc = exc

    def resolve_create(self, *, bot_id, user_id, request):
        return None

    def after_bot_registered(self, *, bot_id, user_id):
        pass

    def before_delete(self, *, bot_id, owner_id):
        pass

    def on_delete_completed(self, *, bot_id, owner_id):
        pass

    def on_delete_not_found(self, *, bot_id, owner_id):
        if self._exc is not None:
            raise self._exc


def _bind_raw_extension(tc, svc, passport, ext):
    """Layer an already-built extension instance over the fixture's Noop."""
    from fastapi_injector import attach_injector as _attach
    from injector import Injector as _Injector, Module as _Module

    class _Bind(Module):
        def configure(self, binder):
            binder.bind(BotCreateExtension, to=ext)

    _attach(tc.app, _Injector([_bind_bot_service(
        svc, bot_repo=MagicMock(), passport=passport,
        auth=MagicMock(), auth_rel=MagicMock(),
        skill_set_factory=MagicMock(),
    ), _Bind()]))
    return tc


class TestDeleteNotFoundEnvelope:
    """The not-found cleanup callback must map like every other hook.

    It runs inside the ``except BotNotFoundError`` handler, so an exception
    raised there escapes the sibling mappings entirely — the route has to
    translate it itself. The not-found proof is the callback's own business
    (an implementation reads the owner-scoped row before touching any
    parent snapshot); the route keeps the 404 envelope whenever the
    callback finishes, and surfaces a controlled 500 whenever it fails.
    """

    @staticmethod
    def _delete_missing_bot(client, mock_passport, ext):
        tc, svc, passport = client
        svc.delete_bot.side_effect = BotNotFoundError("bot missing")
        _bind_raw_extension(tc, svc, passport, ext)
        return tc.delete(DELETE_URL_TPL.format(bot_id="bot-1"))

    def test_healthy_not_found_callback_keeps_404(self, client, mock_passport):
        resp = self._delete_missing_bot(client, mock_passport, NotFoundThrowingExtension())
        body = resp.json()
        assert body["success"] is False
        assert body["error_code"] == 404

    @pytest.mark.parametrize(
        ("exc", "code", "data"),
        [
            pytest.param(
                ExtensionBlocked("关联实例状态暂不可达", data={"bot_id": "b1"}),
                500, {"bot_id": "b1"}, id="blocked-500-with-data",
            ),
            pytest.param(
                RuntimeError("关联存储不可达"),
                500, None, id="unexpected-500-controlled",
            ),
        ],
    )
    def test_failing_not_found_callback_maps_to_envelopes(
        self, client, mock_passport, exc, code, data
    ):
        resp = self._delete_missing_bot(
            client, mock_passport, NotFoundThrowingExtension(exc)
        )
        body = resp.json()
        assert body["success"] is False
        assert body["error_code"] == code
        assert body["data"] == data


class AuthRouteThrowingExtension:
    """Extension double: raises the configured rejection from
    ``resolve_create`` — the first hook the authorization-completion flow
    runs, so the rejection surfaces inside the route's own mapping chain."""

    def __init__(self, exc: Exception | None = None) -> None:
        self._exc = exc

    def resolve_create(self, *, bot_id, user_id, request):
        if self._exc is not None:
            raise self._exc
        return None

    def after_bot_registered(self, *, bot_id, user_id):
        pass

    def before_delete(self, *, bot_id, owner_id):
        pass

    def on_delete_completed(self, *, bot_id, owner_id):
        pass

    def on_delete_not_found(self, *, bot_id, owner_id):
        pass


class TestAuthStatusEnvelope:
    """The authorization-completion route maps the lifecycle-extension
    families exactly like create/delete: invalid input → 400, conflict →
    409, blocked → 500 carrying the extension's data payload."""

    @pytest.mark.parametrize(
        ("exc", "code", "data"),
        [
            pytest.param(
                ExtensionInvalidInput("相关请求上下文不完整"),
                400, None, id="invalid-input-400",
            ),
            pytest.param(
                ExtensionConflict("续接目标与预留不一致"),
                409, None, id="conflict-409",
            ),
            pytest.param(
                ExtensionBlocked("关联实例状态暂不可达", data={"bot_id": "b9"}),
                500, {"bot_id": "b9"}, id="blocked-500-with-data",
            ),
        ],
    )
    def test_extension_rejections_map_to_envelopes(
        self, client, mock_passport, exc, code, data
    ):
        tc, svc, passport = client
        _bind_raw_extension(tc, svc, passport, AuthRouteThrowingExtension(exc))
        resp = tc.post(
            "/api/bots/auth-status",
            json={"bot_id": "bot-9", "bot_name": "NewBot"},
        )
        body = resp.json()
        assert body["success"] is False
        assert body["error_code"] == code
        assert body["data"] == data

    def test_no_rejection_maps_to_pending_envelope(self, client, mock_passport):
        tc, svc, passport = client
        passport.query_auth_status.return_value = {"status": "PENDING"}
        _bind_raw_extension(tc, svc, passport, AuthRouteThrowingExtension())
        body = tc.post(
            "/api/bots/auth-status",
            json={"bot_id": "bot-9", "bot_name": "NewBot"},
        ).json()
        assert body["success"] is True
        assert body["data"]["status"] == "PENDING"
