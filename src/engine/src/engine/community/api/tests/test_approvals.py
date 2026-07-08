"""Phase D — HTTP contract tests for `api/approvals.py`.

Verifies the `/api/approvals/mode/{get,set}` route now flows through
`manager.approval` instead of the deleted `EngineManager.get_client_getter()`
path, and that the capability guard short-circuits 501 when the active engine
doesn't expose ApprovalService (mirrors the AiCoding case).

Mocks the manager singleton so no real gateway client is involved.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.community.core.approval.models import (
    ApprovalModeGetResult,
    ApprovalModeSetResult,
)
from engine.community.core.engine.base import BaseEngine
from engine.community.core.engine.capability import Capability, EngineCapabilities
from engine.community.core.engine.registry import EngineRegistry
from engine.community.manager import EngineManager
from engine.community.api.approvals import router as approvals_router


# ─────────────────────────────────────────────────────────────────────────────
# Engine fixtures — one with approvals, one without (mirrors OpenClaw vs
# AiCoding from the production capability matrix).
# ─────────────────────────────────────────────────────────────────────────────


class _EngineWithApprovals(BaseEngine):
    name = "rich"
    version = "1.0.0"

    _CAPABILITIES = EngineCapabilities(
        supported={Capability.APPROVAL_GET, Capability.APPROVAL_SET},
    )

    @property
    def capabilities(self) -> EngineCapabilities:
        return self._CAPABILITIES

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._session = MagicMock()
        self._chat = MagicMock()
        # _approval will be poked in per-test so we can assert the call.


class _EngineWithoutApprovals(BaseEngine):
    """Mirrors AiCoding — no APPROVAL_GET/SET in declared caps."""

    name = "lean"
    version = "0.1.0"

    _CAPABILITIES = EngineCapabilities(
        supported={Capability.SESSION_LIST, Capability.CHAT_STREAM},
    )

    @property
    def capabilities(self) -> EngineCapabilities:
        return self._CAPABILITIES

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._session = MagicMock()
        self._chat = MagicMock()
        # _approval intentionally left None — the manager's passthrough should
        # never be reached because check_capability 501s first.


# ─────────────────────────────────────────────────────────────────────────────
# Test harness
# ─────────────────────────────────────────────────────────────────────────────


def _install_manager(engine_cls: type[BaseEngine]) -> EngineManager:
    EngineManager.reset_instance()
    registry = EngineRegistry()
    registry.register(engine_cls)
    m = EngineManager(engine_cls.name, registry=registry)
    m._active_engine = engine_cls()
    EngineManager._instance = m
    return m


@pytest.fixture
def rich_manager():
    m = _install_manager(_EngineWithApprovals)
    yield m
    EngineManager.reset_instance()


@pytest.fixture
def lean_manager():
    m = _install_manager(_EngineWithoutApprovals)
    yield m
    EngineManager.reset_instance()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(approvals_router)
    return TestClient(app)


# ─────────────────────────────────────────────────────────────────────────────
# Happy path — engine declares + implements ApprovalService
# ─────────────────────────────────────────────────────────────────────────────


class TestGetMode:
    def test_dispatches_through_manager_approval(self, rich_manager, client):
        plugin = MagicMock()
        plugin.get_mode = AsyncMock(return_value=ApprovalModeGetResult(
            mode="on-miss",
            payload={"mode": "on-miss", "globalDefault": "always"},
        ))
        rich_manager._active_engine._approval = plugin

        resp = client.post(
            "/api/approvals/mode/get",
            json={"user_id": "u1", "session_key": "sk-1"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        # Payload preserved verbatim so the frontend keeps its globalDefault /
        # isOverride hooks (`useApprovals.ts:38-39`).
        assert body["data"] == {"mode": "on-miss", "globalDefault": "always"}

        plugin.get_mode.assert_awaited_once()
        passed = plugin.get_mode.await_args.args[0]
        assert passed.session_key == "sk-1"

    def test_unsupported_engine_returns_501(self, lean_manager, client):
        # AiCoding analog — no APPROVAL_GET in declared caps. The router
        # never reaches `manager.approval`; the guard 501s first.
        resp = client.post(
            "/api/approvals/mode/get",
            json={"user_id": "u1", "session_key": "sk-1"},
        )
        assert resp.status_code == 501


class TestSetMode:
    def test_dispatches_through_manager_approval(self, rich_manager, client):
        plugin = MagicMock()
        plugin.set_mode = AsyncMock(return_value=ApprovalModeSetResult(
            ok=True, mode="never", session_key="sk-2",
        ))
        rich_manager._active_engine._approval = plugin

        resp = client.post(
            "/api/approvals/mode/set",
            json={"user_id": "u1", "session_key": "sk-2", "mode": "never"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"] == {
            "ok": True,
            "mode": "never",
            "sessionKey": "sk-2",
        }

        plugin.set_mode.assert_awaited_once()
        passed = plugin.set_mode.await_args.args[0]
        assert passed.session_key == "sk-2"
        assert passed.mode == "never"

    def test_invalid_mode_rejected_before_dispatch(self, rich_manager, client):
        plugin = MagicMock()
        plugin.set_mode = AsyncMock()
        rich_manager._active_engine._approval = plugin

        resp = client.post(
            "/api/approvals/mode/set",
            json={"user_id": "u1", "session_key": "sk-2", "mode": "bogus"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "Invalid mode" in body["error"]
        # Validator runs before the capability guard / plugin dispatch.
        plugin.set_mode.assert_not_awaited()

    def test_unsupported_engine_returns_501(self, lean_manager, client):
        resp = client.post(
            "/api/approvals/mode/set",
            json={"user_id": "u1", "session_key": "sk-2", "mode": "never"},
        )
        assert resp.status_code == 501


class TestListModes:
    def test_returns_three_static_modes(self, rich_manager, client):
        # Static endpoint — no engine roundtrip.
        resp = client.get("/api/approvals/modes")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        values = {m["value"] for m in body["data"]}
        assert values == {"approve", "on-miss", "never"}
