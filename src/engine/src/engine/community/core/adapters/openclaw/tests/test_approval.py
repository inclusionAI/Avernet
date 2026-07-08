"""Unit tests for the OpenClaw approval ACL adapter.

Drives `OpenClawApprovalAdapter` against a fake `OpenClawApprovalPort` (a plain
object returning canned raw dicts) — the adapter's job is dict→DTO translation
+ !ok error raising, matching the legacy `OpenClawApprovalService` behaviour.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from engine.community.core.adapters.openclaw.approval import OpenClawApprovalAdapter
from engine.community.core.approval.models import (
    ApprovalModeGetRequest,
    ApprovalModeSetRequest,
)
from engine.community.core.engine.context import AuthContext


@dataclass
class _FakeAuth:
    token: str | None = None


def _auth(token: str | None = None) -> AuthContext:
    return _FakeAuth(token=token)  # type: ignore[return-value]


class _FakeApprovalPort:
    """Fake `OpenClawApprovalPort` — returns canned dicts; records calls."""

    def __init__(
        self,
        get_result: dict[str, Any] | None = None,
        set_result: dict[str, Any] | None = None,
    ) -> None:
        self._get_result = get_result or {
            "ok": True,
            "payload": {"mode": "on-miss", "globalDefault": True},
        }
        self._set_result = set_result or {
            "ok": True,
            "payload": {"ok": True},
        }
        self.get_calls: list[dict[str, Any]] = []
        self.set_calls: list[dict[str, Any]] = []

    async def approvals_get(
        self,
        session_key: str | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        self.get_calls.append({"session_key": session_key, "token": token})
        return self._get_result

    async def approvals_set(
        self,
        session_key: str,
        mode: str,
        token: str | None = None,
    ) -> dict[str, Any]:
        self.set_calls.append(
            {"session_key": session_key, "mode": mode, "token": token}
        )
        return self._set_result


# ── get_mode ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_mode_builds_result_from_raw_payload():
    port = _FakeApprovalPort()
    adapter = OpenClawApprovalAdapter(port)
    result = await adapter.get_mode(
        ApprovalModeGetRequest(session_key="sk1"), auth=_auth("tok1")
    )
    assert result.mode == "on-miss"
    assert result.payload["globalDefault"] is True
    assert port.get_calls[0]["session_key"] == "sk1"
    assert port.get_calls[0]["token"] == "tok1"


@pytest.mark.asyncio
async def test_get_mode_no_auth_passes_none_token():
    port = _FakeApprovalPort()
    adapter = OpenClawApprovalAdapter(port)
    await adapter.get_mode(ApprovalModeGetRequest())
    assert port.get_calls[0]["token"] is None
    assert port.get_calls[0]["session_key"] is None


@pytest.mark.asyncio
async def test_get_mode_raises_on_not_ok():
    port = _FakeApprovalPort(
        get_result={"ok": False, "error": "gateway exploded"}
    )
    adapter = OpenClawApprovalAdapter(port)
    with pytest.raises(RuntimeError, match="exec.approvals.get failed: gateway exploded"):
        await adapter.get_mode(ApprovalModeGetRequest(session_key="sk"))


@pytest.mark.asyncio
async def test_get_mode_missing_mode_key_returns_none():
    """When the gateway payload omits `mode`, result.mode is None."""
    port = _FakeApprovalPort(
        get_result={"ok": True, "payload": {"globalDefault": False}}
    )
    adapter = OpenClawApprovalAdapter(port)
    result = await adapter.get_mode(ApprovalModeGetRequest())
    assert result.mode is None


# ── set_mode ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_mode_builds_result_echoes_mode_and_session():
    port = _FakeApprovalPort()
    adapter = OpenClawApprovalAdapter(port)
    result = await adapter.set_mode(
        ApprovalModeSetRequest(session_key="sk2", mode="never"),
        auth=_auth("tok2"),
    )
    assert result.ok is True
    assert result.mode == "never"
    assert result.session_key == "sk2"
    assert port.set_calls[0]["session_key"] == "sk2"
    assert port.set_calls[0]["mode"] == "never"
    assert port.set_calls[0]["token"] == "tok2"


@pytest.mark.asyncio
async def test_set_mode_raises_on_not_ok():
    port = _FakeApprovalPort(
        set_result={"ok": False, "error": "invalid mode"}
    )
    adapter = OpenClawApprovalAdapter(port)
    with pytest.raises(RuntimeError, match="exec.approvals.set failed: invalid mode"):
        await adapter.set_mode(
            ApprovalModeSetRequest(session_key="sk", mode="bad")
        )


@pytest.mark.asyncio
async def test_set_mode_ok_flag_from_payload():
    """When payload.ok is False (edge case), result.ok mirrors it."""
    port = _FakeApprovalPort(
        set_result={"ok": True, "payload": {"ok": False}}
    )
    adapter = OpenClawApprovalAdapter(port)
    result = await adapter.set_mode(
        ApprovalModeSetRequest(session_key="sk", mode="always")
    )
    assert result.ok is False
