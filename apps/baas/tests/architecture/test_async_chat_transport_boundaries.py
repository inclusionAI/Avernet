"""Prevent A2A policy from creeping back into WS and shared orchestration."""

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src"
_BOT_RUN = _SRC / "secbaas/community/core/service/bot_run"


@pytest.mark.parametrize("module", ["_async_chat_client", "_async_chat_events"])
def test_shared_chat_has_no_concrete_transport_dependencies(module):
    tree = ast.parse((_BOT_RUN / f"{module}.py").read_text())
    forbidden = {
        "_bot_websocket_client",
        "_bot_a2a_client",
        "_async_chat_client_ws",
        "_async_chat_client_a2a",
        "httpx",
        "websockets",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not forbidden.intersection((node.module or "").split("."))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert not forbidden.intersection(alias.name.split("."))
        elif isinstance(node, ast.Attribute):
            assert node.attr not in {
                "routed_sessions",
                "fallback_client",
                "refresh_proxypass_token",
                "_proxypass_token_minter",
            }


@pytest.mark.parametrize("module", ["_async_chat_client_ws", "_bot_websocket_client"])
def test_ws_has_no_a2a_dependencies_or_policy(module):
    tree = ast.parse((_BOT_RUN / f"{module}.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert "a2a" not in (node.module or "").lower()
        elif isinstance(node, ast.Import):
            assert all("a2a" not in alias.name.lower() for alias in node.names)
        elif isinstance(node, ast.Attribute):
            assert node.attr not in {
                "routed_sessions",
                "fallback_client",
                "_proxypass_token_minter",
            }


def test_off_mode_works_when_a2a_modules_cannot_be_imported():
    # A fresh interpreter avoids import-cache false positives from the A2A
    # suites collected in the main pytest process.
    script = """
import asyncio
import importlib.abc
import sys
from unittest.mock import AsyncMock, MagicMock, patch

class RejectA2a(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.endswith(("._bot_a2a_client", "._async_chat_client_a2a")):
            raise AssertionError("WS imported A2A: " + fullname)

sys.meta_path.insert(0, RejectA2a())
from secbaas.community.core.service.bot_run._async_chat_client import AsyncChatClient

async def run():
    wire = MagicMock()
    wire.connected = True
    wire.connect = AsyncMock(return_value={})
    wire.close = AsyncMock()
    wire.chat_send = AsyncMock(return_value={"ok": True})
    wire.chat_inject = AsyncMock(return_value={"ok": True})
    wire.chat_abort = AsyncMock(return_value={"ok": True})
    minter = AsyncMock(side_effect=AssertionError("WS minted an HTTP token"))
    with patch("secbaas.community.core.service.bot_run._async_chat_client_ws.BotWebSocketClient", return_value=wire):
        client = AsyncChatClient(uri="ws://example.test/ws", transport_mode="off", max_retries=0, proxypass_token_minter=minter)
        await client.connect()
        await client.send_message("hello", session_key="sk", wait_result=False)
        await client.inject_message("inject", session_key="sk")
        await client.chat_abort(session_key="sk")
        client.refresh_outbound_auth({"x-proxypass-token": "placeholder"})
        wire.refresh_proxypass_token.assert_not_called()
        minter.assert_not_awaited()
        await client.close()

asyncio.run(run())
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "PYTHONPATH": str(_SRC)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
