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
