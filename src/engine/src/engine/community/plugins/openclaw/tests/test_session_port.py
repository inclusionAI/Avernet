"""Port-impl tests for OpenClawPluginImpl session transport methods.

Drives OpenClawPluginImpl against a fake pool→gateway client to pin the
SESSION transport at the port layer.  Asserts RAW dict returns (not core
Session DTOs — those are covered by core/adapters/openclaw/tests/test_session.py).

Coverage preserved from engines/openclaw/tests/test_session.py (legacy):
  - sessions_list: sessions.list + per-page chat.history + providers.available
  - sessions_list: bcs:group filter with bcs_grp DM/bcs-cli exceptions,
    "Bot 初始化配置" filter
  - sessions_list: pagination (offset/limit BEFORE history fetch)
  - sessions_list: model normalization via _normalized_model key
  - session_create: sessions.patch RPC method + params + raw return
  - session_create: raises RuntimeError on gateway error
  - session_delete: sessions.delete RPC method + params + bool return
  - session_clear: sessions.reset RPC method + RuntimeError on error
  - session_reset: in-band {success, error, payload} shapes
  - chat_history: chat.history RPC + raw message list return
  - session_patch_then_get: patch → list → find-by-key flow
  - session_patch_then_get: synthesises a dict when key not found after patch
"""
from __future__ import annotations

from typing import Any

import pytest

from engine.community.kernel.frames import ResponseFrame
from engine.community.plugins.openclaw.plugin_impl import OpenClawPluginImpl


# ── Fake transport layer ──────────────────────────────────────────────────────


class _FakeClient:
    """Minimal gateway-client double; routes send_request by method string."""

    def __init__(self, responses: dict[str, Any]) -> None:
        """``responses`` maps method string → ResponseFrame (or a list of them
        when a method is called multiple times in sequence).
        """
        self.connected = True
        self._responses = responses
        # Track every send_request call as (method, params, timeout).
        self.calls: list[tuple[str, Any, float | None]] = []
        self._counters: dict[str, int] = {}

    async def send_request(
        self,
        method: str,
        params: Any = None,
        timeout: float | None = None,
    ) -> ResponseFrame:
        self.calls.append((method, params, timeout))
        val = self._responses.get(method)
        if val is None:
            # Default: not-ok response with generic error
            return ResponseFrame(
                id="1",
                ok=False,
                error=None,
            )
        if isinstance(val, list):
            idx = self._counters.get(method, 0)
            frame = val[min(idx, len(val) - 1)]
            self._counters[method] = idx + 1
            return frame
        return val

    async def session_reset(self, session_key: str) -> dict[str, Any]:
        """Delegates to send_request like the real client does."""
        response = await self.send_request("sessions.reset", {"key": session_key})
        if response.ok:
            return {"success": True, "payload": response.payload}
        error = response.error
        return {
            "success": False,
            "error": error.to_dict() if error else {"code": "UNKNOWN", "message": "Unknown error"},
        }


class _FakePool:
    """Pool double: get(token) always returns the one fake client."""

    def __init__(self, client: _FakeClient) -> None:
        self._client = client

    async def get(self, token: str | None = None) -> _FakeClient:
        return self._client


def _make_impl(responses: dict[str, Any]) -> tuple[OpenClawPluginImpl, _FakeClient]:
    client = _FakeClient(responses)
    impl = OpenClawPluginImpl(pool=_FakePool(client))
    return impl, client


def _ok(payload: Any) -> ResponseFrame:
    return ResponseFrame(id="1", ok=True, payload=payload)


def _err(message: str = "gateway error") -> ResponseFrame:
    from engine.community.kernel.frames import ErrorShape
    return ResponseFrame(
        id="1",
        ok=False,
        error=ErrorShape(code="ERROR", message=message),
    )


# ── sessions_list ─────────────────────────────────────────────────────────────


class TestSessionsList:
    """Orchestration tests for sessions_list (port-level raw dict asserts)."""

    def _sessions_payload(self, sessions: list[dict]) -> ResponseFrame:
        return _ok({"sessions": sessions})

    def _history_payload(self, messages: list[dict]) -> ResponseFrame:
        return _ok({"messages": messages})

    def _providers_payload(self) -> ResponseFrame:
        return _ok({
            "providers": [
                {
                    "id": "antchat",
                    "name": "AntChat",
                    "models": [
                        {"id": "qwen-plus"},
                        {"id": "qwen-max"},
                    ],
                }
            ]
        })

    async def test_returns_empty_list_on_gateway_error(self):
        impl, _ = _make_impl({"sessions.list": _err()})
        result = await impl.sessions_list(token="tok")
        assert result == []

    @pytest.mark.parametrize(
        "error", [ConnectionError("disconnected"), TimeoutError("timed out")]
    )
    async def test_propagates_transport_failures(self, error: Exception):
        class _TransportErrorClient:
            connected = True

            async def send_request(self, method, params=None, timeout=None):
                raise error

        class _TransportErrorPool:
            async def get(self, token=None):
                return _TransportErrorClient()

        impl = OpenClawPluginImpl(pool=_TransportErrorPool())

        with pytest.raises(type(error), match=str(error)):
            await impl.sessions_list(token="tok")

    async def test_basic_session_returned_with_messages(self):
        sessions = [{"key": "s1", "label": "Test", "model": "qwen-plus"}]
        messages = [{"id": "m1", "role": "user", "content": "hi"}]
        impl, client = _make_impl({
            "sessions.list": self._sessions_payload(sessions),
            "chat.history": self._history_payload(messages),
            "providers.available": self._providers_payload(),
        })
        result = await impl.sessions_list(token="tok")
        assert len(result) == 1
        s = result[0]
        assert s["key"] == "s1"
        assert s["_message_count"] == 1
        assert s["_messages"] == messages

    async def test_bcs_group_sessions_hide_group_chats_and_keep_dm_sessions(self):
        """BCS group chats are hidden while bcs_grp DM sessions remain visible."""
        token = "bc7d52974947474da2f1cdea1c5642b6"
        channel_dm_key = f"agent:main:bcs:group:bcs_grp_dingtalk_dm_{token}"
        native_dm_key = f"agent:main:bcs:group:bcs_grp_dm_{token}"
        flexible_dm_key = "agent:main:bcs:group:bcs_grp_dingtalk_dm_not-a-token"
        sessions = [
            {"key": None, "label": "Null key"},
            {"label": "Missing key"},
            {"key": "bcs:group:room-42", "label": "Group"},
            {
                "key": f"agent:main:bcs:group:bcs_grp_dingtalk_{token}",
                "label": "DingTalk group",
            },
            {"key": channel_dm_key, "label": "DingTalk DM"},
            {"key": native_dm_key, "label": "Native DM"},
            {"key": flexible_dm_key, "label": "Flexible DM"},
            {"key": "agent:main:bcs:group:legacy_dm_session", "label": "Legacy group"},
            {"key": "bcs:group:bcs-cli", "label": "CLI"},  # allowed through
            {"key": "normal-session", "label": "Normal"},
        ]
        impl, client = _make_impl({
            "sessions.list": self._sessions_payload(sessions),
            "chat.history": self._history_payload([{"id": "m1", "role": "user", "content": "x"}]),
            "providers.available": self._providers_payload(),
        })
        result = await impl.sessions_list(token="tok")
        keys = [s["key"] for s in result]
        assert "bcs:group:room-42" not in keys
        assert f"agent:main:bcs:group:bcs_grp_dingtalk_{token}" not in keys
        assert "agent:main:bcs:group:legacy_dm_session" not in keys
        assert keys == [
            channel_dm_key,
            native_dm_key,
            flexible_dm_key,
            "bcs:group:bcs-cli",
            "normal-session",
        ]
        history_keys = [
            params["sessionKey"]
            for method, params, _ in client.calls
            if method == "chat.history"
        ]
        assert history_keys == keys

    async def test_bot_init_config_single_message_filtered_out(self):
        """Sessions labelled 'Bot 初始化配置' with exactly one message are filtered."""
        sessions = [
            {"key": "s-init", "label": "Bot 初始化配置", "model": None},
            {"key": "s-normal", "label": "Normal", "model": None},
        ]
        # Each session gets the same single-message history
        impl, _ = _make_impl({
            "sessions.list": self._sessions_payload(sessions),
            "chat.history": self._history_payload([{"id": "m1", "role": "user", "content": "init"}]),
            "providers.available": self._providers_payload(),
        })
        result = await impl.sessions_list(token="tok")
        keys = [s["key"] for s in result]
        assert "s-init" not in keys
        assert "s-normal" in keys

    async def test_bot_init_config_multi_message_not_filtered(self):
        """'Bot 初始化配置' with >1 messages is kept (not a bare-init session)."""
        sessions = [{"key": "s-init", "label": "Bot 初始化配置", "model": None}]
        impl, _ = _make_impl({
            "sessions.list": self._sessions_payload(sessions),
            "chat.history": self._history_payload([
                {"id": "m1", "role": "user", "content": "a"},
                {"id": "m2", "role": "assistant", "content": "b"},
            ]),
            "providers.available": self._providers_payload(),
        })
        result = await impl.sessions_list(token="tok")
        assert len(result) == 1
        assert result[0]["key"] == "s-init"

    async def test_pagination_offset_applied_before_history_fetch(self):
        """offset/limit slices the raw list BEFORE chat.history is fetched per session."""
        sessions = [{"key": f"s{i}", "label": f"Session {i}", "model": None} for i in range(5)]
        impl, client = _make_impl({
            "sessions.list": self._sessions_payload(sessions),
            "chat.history": self._history_payload([
                {"id": "m1", "role": "user", "content": "hi"},
                {"id": "m2", "role": "assistant", "content": "hello"},
            ]),
            "providers.available": self._providers_payload(),
        })
        result = await impl.sessions_list(token="tok", offset=2, limit=2)
        # Only sessions s2 and s3 should be present (offset=2, limit=2)
        keys = [s["key"] for s in result]
        assert keys == ["s2", "s3"]
        # chat.history was called exactly twice (once per page session)
        history_calls = [c for c in client.calls if c[0] == "chat.history"]
        assert len(history_calls) == 2
        assert history_calls[0][1]["sessionKey"] == "s2"
        assert history_calls[1][1]["sessionKey"] == "s3"

    async def test_session_key_filter_applied_before_pagination(self):
        sessions = [
            {"key": f"s{i}", "label": f"Session {i}", "model": None}
            for i in range(21)
        ]
        impl, client = _make_impl({
            "sessions.list": self._sessions_payload(sessions),
            "chat.history": self._history_payload([
                {"id": "m1", "role": "user", "content": "hi"},
            ]),
            "providers.available": self._providers_payload(),
        })

        result = await impl.sessions_list(token="tok", session_key="s20", limit=1)

        assert [session["key"] for session in result] == ["s20"]
        history_calls = [call for call in client.calls if call[0] == "chat.history"]
        assert [call[1]["sessionKey"] for call in history_calls] == ["s20"]

    async def test_session_key_filter_returns_empty_list_for_no_match(self):
        impl, client = _make_impl({
            "sessions.list": self._sessions_payload([
                {"key": "s1", "label": "Session 1", "model": None},
            ]),
            "providers.available": self._providers_payload(),
        })

        result = await impl.sessions_list(token="tok", session_key="missing")

        assert result == []
        assert not [call for call in client.calls if call[0] == "chat.history"]

    async def test_blank_session_key_keeps_existing_pagination(self):
        sessions = [
            {"key": "s0", "label": "Session 0", "model": None},
            {"key": "s1", "label": "Session 1", "model": None},
        ]
        impl, _ = _make_impl({
            "sessions.list": self._sessions_payload(sessions),
            "chat.history": self._history_payload([
                {"id": "m1", "role": "user", "content": "hi"},
            ]),
            "providers.available": self._providers_payload(),
        })

        result = await impl.sessions_list(token="tok", session_key="   ", offset=1, limit=1)

        assert [session["key"] for session in result] == ["s1"]

    async def test_model_normalization_present(self):
        """_normalized_model is set for each returned session dict."""
        sessions = [{"key": "s1", "label": "S1", "model": "qwen-plus"}]
        impl, _ = _make_impl({
            "sessions.list": self._sessions_payload(sessions),
            "chat.history": self._history_payload([
                {"id": "m1", "role": "user", "content": "hi"},
                {"id": "m2", "role": "assistant", "content": "hello"},
            ]),
            "providers.available": self._providers_payload(),
        })
        result = await impl.sessions_list(token="tok")
        assert len(result) == 1
        assert "_normalized_model" in result[0]
        # qwen-plus is in the providers map → should be prefixed
        assert result[0]["_normalized_model"] == "antchat/qwen-plus"

    async def test_model_normalization_already_qualified_passthrough(self):
        """A model already containing '/' is returned as-is."""
        sessions = [{"key": "s1", "label": "S1", "model": "antchat/qwen-plus"}]
        impl, _ = _make_impl({
            "sessions.list": self._sessions_payload(sessions),
            "chat.history": self._history_payload([
                {"id": "m1", "role": "user", "content": "hi"},
            ]),
            "providers.available": self._providers_payload(),
        })
        result = await impl.sessions_list(token="tok")
        assert result[0]["_normalized_model"] == "antchat/qwen-plus"

    async def test_model_normalization_uses_row_model_provider(self):
        """A bare model + row modelProvider rebuilds provider/model directly.

        Regression: gateway sessions.list returns model as a bare id with the
        provider in the separate `modelProvider` field. A non-antchat provider
        (e.g. modelmng) must be preserved, NOT rewritten to the antchat/ default
        fallback.
        """
        sessions = [{
            "key": "s1",
            "label": "S1",
            "model": "6651a7445468477a85a2af38cf0fa029",
            "modelProvider": "modelmng",
        }]
        impl, _ = _make_impl({
            "sessions.list": self._sessions_payload(sessions),
            "chat.history": self._history_payload([
                {"id": "m1", "role": "user", "content": "hi"},
            ]),
            "providers.available": self._providers_payload(),
        })
        result = await impl.sessions_list(token="tok")
        assert result[0]["_normalized_model"] == "modelmng/6651a7445468477a85a2af38cf0fa029"

    async def test_provider_map_built_once_per_impl(self):
        """providers.available is called at most once per impl instance."""
        sessions = [
            {"key": "s1", "label": "S1", "model": None},
            {"key": "s2", "label": "S2", "model": None},
        ]
        impl, client = _make_impl({
            "sessions.list": self._sessions_payload(sessions),
            "chat.history": self._history_payload([
                {"id": "m1", "role": "user", "content": "hi"},
                {"id": "m2", "role": "assistant", "content": "hello"},
            ]),
            "providers.available": self._providers_payload(),
        })
        # Two calls to sessions_list should only fetch providers.available once total.
        await impl.sessions_list(token="tok")
        await impl.sessions_list(token="tok")
        prov_calls = [c for c in client.calls if c[0] == "providers.available"]
        assert len(prov_calls) == 1

    async def test_agent_id_filter(self):
        """agent_id filter limits sessions to those with matching agentId."""
        sessions = [
            {"key": "s1", "agentId": "agent-a", "label": "A"},
            {"key": "s2", "agentId": "agent-b", "label": "B"},
        ]
        impl, _ = _make_impl({
            "sessions.list": self._sessions_payload(sessions),
            "chat.history": self._history_payload([
                {"id": "m1", "role": "user", "content": "hi"},
                {"id": "m2", "role": "assistant", "content": "hello"},
            ]),
            "providers.available": self._providers_payload(),
        })
        result = await impl.sessions_list(token="tok", agent_id="agent-a")
        assert len(result) == 1
        assert result[0]["key"] == "s1"

    async def test_list_payload_as_bare_list(self):
        """sessions.list may return a bare list (not wrapped in {sessions:[]})."""
        sessions = [{"key": "s1", "label": "Direct", "model": None}]
        impl, _ = _make_impl({
            "sessions.list": _ok(sessions),
            "chat.history": self._history_payload([
                {"id": "m1", "role": "user", "content": "hi"},
            ]),
            "providers.available": self._providers_payload(),
        })
        result = await impl.sessions_list(token="tok")
        assert len(result) == 1
        assert result[0]["key"] == "s1"

    async def test_sessions_list_sends_correct_rpc(self):
        """sessions.list is called with an empty params dict {}."""
        impl, client = _make_impl({
            "sessions.list": _ok([]),
            "providers.available": self._providers_payload(),
        })
        await impl.sessions_list(token="tok")
        sl_calls = [c for c in client.calls if c[0] == "sessions.list"]
        assert len(sl_calls) == 1
        assert sl_calls[0][1] == {}


# ── session_create ────────────────────────────────────────────────────────────


class TestSessionCreate:
    async def test_sends_sessions_patch_with_key(self):
        impl, client = _make_impl({"sessions.patch": _ok({})})
        await impl.session_create(key="new-key", token="tok")
        patch_calls = [c for c in client.calls if c[0] == "sessions.patch"]
        assert len(patch_calls) == 1
        assert patch_calls[0][1]["key"] == "new-key"
        assert patch_calls[0][2] == 60.0

    async def test_sends_label_and_model_when_provided(self):
        impl, client = _make_impl({"sessions.patch": _ok({})})
        await impl.session_create(key="k", label="My Session", model="qwen-plus", token="tok")
        params = client.calls[0][1]
        assert params["label"] == "My Session"
        assert params["model"] == "qwen-plus"

    async def test_omits_label_and_model_when_none(self):
        impl, client = _make_impl({"sessions.patch": _ok({})})
        await impl.session_create(key="k", token="tok")
        params = client.calls[0][1]
        assert "label" not in params
        assert "model" not in params

    async def test_returns_patch_params_dict(self):
        impl, _ = _make_impl({"sessions.patch": _ok({})})
        result = await impl.session_create(key="k", label="L", token="tok")
        assert result == {"key": "k", "label": "L"}

    async def test_raises_runtime_error_on_gateway_failure(self):
        impl, _ = _make_impl({"sessions.patch": _err("patch failed")})
        with pytest.raises(RuntimeError, match="patch failed"):
            await impl.session_create(key="k", token="tok")


# ── session_delete ────────────────────────────────────────────────────────────


class TestSessionDelete:
    async def test_sends_sessions_delete_with_key(self):
        impl, client = _make_impl({"sessions.delete": _ok({})})
        await impl.session_delete(key="del-key", token="tok")
        del_calls = [c for c in client.calls if c[0] == "sessions.delete"]
        assert len(del_calls) == 1
        assert del_calls[0][1] == {"key": "del-key"}

    async def test_returns_true_on_success(self):
        impl, _ = _make_impl({"sessions.delete": _ok({})})
        result = await impl.session_delete(key="k", token="tok")
        assert result is True

    async def test_returns_false_on_gateway_error(self):
        impl, _ = _make_impl({"sessions.delete": _err()})
        result = await impl.session_delete(key="k", token="tok")
        assert result is False

    async def test_returns_false_on_connection_error(self):
        """ConnectionError from send_request is caught and returns False."""

        class _ConnErrClient:
            connected = True

            async def send_request(self, method, params=None, timeout=None):
                raise ConnectionError("no connection")

        class _ConnErrPool:
            async def get(self, token=None):
                return _ConnErrClient()

        impl = OpenClawPluginImpl(pool=_ConnErrPool())
        result = await impl.session_delete(key="k", token="tok")
        assert result is False


# ── session_clear ─────────────────────────────────────────────────────────────


class TestSessionClear:
    async def test_sends_sessions_reset_with_key(self):
        impl, client = _make_impl({"sessions.reset": _ok({})})
        await impl.session_clear(key="clear-key", token="tok")
        reset_calls = [c for c in client.calls if c[0] == "sessions.reset"]
        assert len(reset_calls) == 1
        assert reset_calls[0][1] == {"key": "clear-key"}

    async def test_returns_none_on_success(self):
        impl, _ = _make_impl({"sessions.reset": _ok({})})
        result = await impl.session_clear(key="k", token="tok")
        assert result is None

    async def test_raises_runtime_error_on_gateway_failure(self):
        impl, _ = _make_impl({"sessions.reset": _err("reset failed")})
        with pytest.raises(RuntimeError, match="reset failed"):
            await impl.session_clear(key="k", token="tok")


# ── session_reset ─────────────────────────────────────────────────────────────


class TestSessionReset:
    """session_reset wraps client.session_reset; always returns dict, never raises."""

    async def test_success_returns_success_true_with_payload(self):
        impl, _ = _make_impl({
            "sessions.reset": _ok({"cleared": True}),
        })
        result = await impl.session_reset(session_key="sk", token="tok")
        assert result["success"] is True
        assert result["payload"] == {"cleared": True}

    async def test_error_returns_success_false_with_error(self):
        impl, _ = _make_impl({
            "sessions.reset": _err("reset RPC error"),
        })
        result = await impl.session_reset(session_key="sk", token="tok")
        assert result["success"] is False
        assert "error" in result
        assert result["error"]["message"] == "reset RPC error"

    async def test_connect_exception_returns_success_false(self):
        """Pool failure also produces a synthetic failure dict (never raises)."""

        class _ErrorPool:
            async def get(self, token=None):
                raise RuntimeError("pool down")

        impl = OpenClawPluginImpl(pool=_ErrorPool())
        result = await impl.session_reset(session_key="sk", token="tok")
        assert result["success"] is False
        assert result["error"]["code"] == "INTERNAL_ERROR"

    async def test_rpc_exception_returns_success_false(self):
        """An exception from client.session_reset is caught and returned as failure dict."""

        class _ExplodingClient:
            connected = True

            async def session_reset(self, session_key: str):
                raise RuntimeError("rpc boom")

            async def send_request(self, method, params=None, timeout=None):
                raise RuntimeError("rpc boom")

        class _ExplodingPool:
            async def get(self, token=None):
                return _ExplodingClient()

        impl = OpenClawPluginImpl(pool=_ExplodingPool())
        result = await impl.session_reset(session_key="sk", token="tok")
        assert result["success"] is False
        assert result["error"]["code"] == "INTERNAL_ERROR"


# ── chat_history ──────────────────────────────────────────────────────────────


class TestChatHistory:
    async def test_returns_message_list(self):
        messages = [
            {"id": "m1", "role": "user", "content": "hello"},
            {"id": "m2", "role": "assistant", "content": "hi"},
        ]
        impl, _ = _make_impl({"chat.history": _ok({"messages": messages})})
        result = await impl.chat_history(session_key="sk", token="tok")
        assert result == messages

    async def test_bare_list_payload_accepted(self):
        messages = [{"id": "m1", "role": "user", "content": "x"}]
        impl, _ = _make_impl({"chat.history": _ok(messages)})
        result = await impl.chat_history(session_key="sk", token="tok")
        assert result == messages

    async def test_sends_session_key_param(self):
        impl, client = _make_impl({"chat.history": _ok([])})
        await impl.chat_history(session_key="my-session", token="tok")
        calls = [c for c in client.calls if c[0] == "chat.history"]
        assert calls[0][1]["sessionKey"] == "my-session"

    async def test_sends_limit_when_provided(self):
        impl, client = _make_impl({"chat.history": _ok([])})
        await impl.chat_history(session_key="sk", limit=42, token="tok")
        calls = [c for c in client.calls if c[0] == "chat.history"]
        assert calls[0][1]["limit"] == 42

    async def test_omits_limit_when_none(self):
        impl, client = _make_impl({"chat.history": _ok([])})
        await impl.chat_history(session_key="sk", token="tok")
        calls = [c for c in client.calls if c[0] == "chat.history"]
        assert "limit" not in calls[0][1]

    async def test_returns_empty_list_on_gateway_error(self):
        impl, _ = _make_impl({"chat.history": _err()})
        result = await impl.chat_history(session_key="sk", token="tok")
        assert result == []

    async def test_returns_empty_list_on_connection_error(self):
        class _ConnErrClient:
            connected = True

            async def send_request(self, method, params=None, timeout=None):
                raise ConnectionError("no connection")

        class _ConnErrPool:
            async def get(self, token=None):
                return _ConnErrClient()

        impl = OpenClawPluginImpl(pool=_ConnErrPool())
        result = await impl.chat_history(session_key="sk", token="tok")
        assert result == []


# ── session_patch_then_get ────────────────────────────────────────────────────


class TestSessionPatchThenGet:
    """Patch → list → find-by-key orchestration."""

    def _sessions_list_resp(self, sessions: list[dict]) -> ResponseFrame:
        return _ok({"sessions": sessions})

    async def test_patch_then_find_returns_updated_session(self):
        updated = {"key": "s1", "label": "Updated Label", "model": "qwen-max"}
        impl, client = _make_impl({
            "sessions.patch": _ok({}),
            "sessions.list": self._sessions_list_resp([updated]),
            "chat.history": _ok({"messages": [{"id": "m1", "role": "user", "content": "hi"}]}),
            "providers.available": _ok({"providers": []}),
        })
        result = await impl.session_patch_then_get(
            key="s1", label="Updated Label", token="tok"
        )
        assert result["key"] == "s1"
        assert result["label"] == "Updated Label"

    async def test_sends_patch_with_label_and_model(self):
        updated = {"key": "s1", "label": "L", "model": "m"}
        impl, client = _make_impl({
            "sessions.patch": _ok({}),
            "sessions.list": self._sessions_list_resp([updated]),
            "chat.history": _ok({"messages": [{"id": "m1", "role": "user", "content": "hi"}]}),
            "providers.available": _ok({"providers": []}),
        })
        await impl.session_patch_then_get(key="s1", label="L", model="m", token="tok")
        patch_calls = [c for c in client.calls if c[0] == "sessions.patch"]
        assert patch_calls[0][1] == {"key": "s1", "label": "L", "model": "m"}

    async def test_raises_runtime_error_when_patch_fails(self):
        impl, _ = _make_impl({"sessions.patch": _err("patch failed")})
        with pytest.raises(RuntimeError, match="patch failed"):
            await impl.session_patch_then_get(key="s1", token="tok")

    async def test_synthesises_dict_when_key_not_found_after_patch(self):
        # Patch succeeds but the session is absent from the follow-up list.
        # Legacy parity: the patch already succeeded, so the port returns a
        # best-effort synthetic dict from the patch params (it must NOT raise —
        # raising would turn a successful patch into a caller-visible failure).
        impl, _ = _make_impl({
            "sessions.patch": _ok({}),
            "sessions.list": self._sessions_list_resp([
                {"key": "other-key", "label": "Other"}
            ]),
            "chat.history": _ok({"messages": [{"id": "m1", "role": "user", "content": "hi"}]}),
            "providers.available": _ok({"providers": []}),
        })
        result = await impl.session_patch_then_get(
            key="s1", label="New Label", model="qwen-max", token="tok"
        )
        assert result == {"key": "s1", "label": "New Label", "model": "qwen-max"}

    async def test_patch_sends_only_key_when_no_label_or_model(self):
        updated = {"key": "s1", "label": "Existing"}
        impl, client = _make_impl({
            "sessions.patch": _ok({}),
            "sessions.list": self._sessions_list_resp([updated]),
            "chat.history": _ok({"messages": [{"id": "m1", "role": "user", "content": "hi"}]}),
            "providers.available": _ok({"providers": []}),
        })
        await impl.session_patch_then_get(key="s1", token="tok")
        patch_calls = [c for c in client.calls if c[0] == "sessions.patch"]
        assert patch_calls[0][1] == {"key": "s1"}
        assert "label" not in patch_calls[0][1]
        assert "model" not in patch_calls[0][1]
