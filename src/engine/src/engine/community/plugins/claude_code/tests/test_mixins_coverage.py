"""Coverage for the claude_code community domain mixins.

Each mixin method is a thin relay forwarder: it calls ``self._relay()``
(``ClaudeCodeRelayClient``) then maps the ``ResponseFrame`` into a
dict/list/bool/None shape. These tests pin the RPC method name, the
params shape, and the response mapping for both success and error paths,
plus the branch points (optional params, payload-is-list vs payload-is-dict,
filter helpers, fallback scans, exception → in-band error).

Reuses the ``_FakeRelayClient`` / ``_impl`` pattern from
``test_community_transport.py`` (copied to keep this file standalone).
"""
from __future__ import annotations

from typing import Any

import pytest

from engine.community.kernel.frames import EventFrame


# ── helpers (copied from test_community_transport.py) ────────────────────────


class _FakeRelayClient:
    def __init__(self) -> None:
        self.connected = True
        self.calls: list[tuple[str, tuple, dict]] = []
        self._responses: dict[str, Any] = {}
        self._events: list[dict[str, Any]] = []
        self.hello = None
        # When set, send_request* raise this instead of returning a response.
        self._raise: Exception | None = None

    def set_response(self, method: str, resp: Any) -> None:
        self._responses[method] = resp

    def set_raise(self, exc: Exception) -> None:
        self._raise = exc

    async def chat_stream(self, **kwargs: Any):
        self.calls.append(("chat_stream", (), dict(kwargs)))
        for e in self._events:
            yield dict(e)

    async def send_request(self, method: str, params: dict | None = None,
                           timeout: float = 30.0) -> Any:
        self.calls.append(("send_request", (method,), {"params": params, "timeout": timeout}))
        if self._raise is not None:
            raise self._raise
        return self._responses.get(method, _ok({}))

    async def send_request_with_events(self, method, params, event_names,
                                       session_key=None, response_timeout=30.0):
        self.calls.append(("send_request_with_events", (method,),
                           {"params": params, "event_names": event_names,
                            "session_key": session_key}))
        if self._raise is not None:
            raise self._raise
        return self._responses.get(method, (_ok({}), []))

    async def send_request_with_id(self, request_id, method, params, timeout=30.0):
        self.calls.append(("send_request_with_id", (method,),
                           {"request_id": request_id, "params": params, "timeout": timeout}))
        if self._raise is not None:
            raise self._raise
        return self._responses.get(method, _ok({}))


def _ok(payload: Any) -> Any:
    from engine.community.kernel.frames import ResponseFrame
    return ResponseFrame(id="x", ok=True, payload=payload)


def _err(code: str, message: str) -> Any:
    from engine.community.kernel.frames import ErrorShape, ResponseFrame
    return ResponseFrame(id="x", ok=False, error=ErrorShape(code=code, message=message))


def _impl(client: _FakeRelayClient | None = None) -> tuple[Any, _FakeRelayClient]:
    from engine.community.plugins.claude_code.plugin_impl import ClaudeCodePluginImpl
    c = client or _FakeRelayClient()
    return ClaudeCodePluginImpl(client=c), c


def _last_call(client: _FakeRelayClient) -> tuple[str, tuple, dict]:
    return client.calls[-1]


# ── _mcp.py ──────────────────────────────────────────────────────────────────


class TestMcpMixin:
    async def test_list_servers_success_dict_payload(self):
        c = _FakeRelayClient()
        c.set_response("mcp.config.list", _ok({"servers": [{"serverCode": "s1"}, "x", {"name": "n"}]}))
        impl, _ = _impl(c)
        out = await impl.mcp_list_servers(token="t")
        assert out == [{"serverCode": "s1"}, {"name": "n"}]
        method, args, kw = _last_call(c)
        assert method == "send_request" and args[0] == "mcp.config.list"
        assert kw["params"] == {}

    async def test_list_servers_success_list_payload(self):
        c = _FakeRelayClient()
        c.set_response("mcp.config.list", _ok([{"serverCode": "s1"}]))
        impl, _ = _impl(c)
        assert await impl.mcp_list_servers() == [{"serverCode": "s1"}]

    async def test_list_servers_error_returns_empty(self):
        c = _FakeRelayClient()
        c.set_response("mcp.config.list", _err("BOOM", "x"))
        impl, _ = _impl(c)
        assert await impl.mcp_list_servers() == []

    async def test_list_servers_non_list_payload(self):
        c = _FakeRelayClient()
        c.set_response("mcp.config.list", _ok({"servers": "not-a-list"}))
        impl, _ = _impl(c)
        assert await impl.mcp_list_servers() == []

    async def test_get_server_success(self):
        c = _FakeRelayClient()
        c.set_response("mcp.config.get", _ok({"serverCode": "s1", "name": "n"}))
        impl, _ = _impl(c)
        out = await impl.mcp_get_server(server_code="s1")
        assert out == {"serverCode": "s1", "name": "n"}
        _, args, kw = _last_call(c)
        assert args[0] == "mcp.config.get" and kw["params"] == {"serverCode": "s1"}

    async def test_get_server_error_returns_none(self):
        c = _FakeRelayClient()
        c.set_response("mcp.config.get", _err("NOPE", "x"))
        impl, _ = _impl(c)
        assert await impl.mcp_get_server(server_code="s1") is None

    async def test_get_server_non_dict_payload_returns_none(self):
        c = _FakeRelayClient()
        c.set_response("mcp.config.get", _ok(["not", "dict"]))
        impl, _ = _impl(c)
        assert await impl.mcp_get_server(server_code="s1") is None

    async def test_get_server_empty_payload_returns_none(self):
        c = _FakeRelayClient()
        c.set_response("mcp.config.get", _ok(None))
        impl, _ = _impl(c)
        assert await impl.mcp_get_server(server_code="s1") is None

    async def test_create_server_success_returns_payload(self):
        cfg = {"serverCode": "s1", "name": "n"}
        c = _FakeRelayClient()
        c.set_response("mcp.config.create", _ok({"serverCode": "s1", "created": True}))
        impl, _ = _impl(c)
        out = await impl.mcp_create_server(config=cfg)
        assert out == {"serverCode": "s1", "created": True}
        _, args, kw = _last_call(c)
        assert args[0] == "mcp.config.create" and kw["params"] is cfg

    async def test_create_server_success_non_dict_returns_config(self):
        cfg = {"serverCode": "s1"}
        c = _FakeRelayClient()
        c.set_response("mcp.config.create", _ok("just-a-string"))
        impl, _ = _impl(c)
        assert await impl.mcp_create_server(config=cfg) is cfg

    async def test_create_server_already_exists_raises_fileexistserror(self):
        c = _FakeRelayClient()
        c.set_response("mcp.config.create", _err("CONFLICT", "server already exists"))
        impl, _ = _impl(c)
        with pytest.raises(FileExistsError):
            await impl.mcp_create_server(config={})

    async def test_create_server_already_exists_chinese_raises_fileexistserror(self):
        c = _FakeRelayClient()
        c.set_response("mcp.config.create", _err("CONFLICT", "服务器已存在"))
        impl, _ = _impl(c)
        with pytest.raises(FileExistsError):
            await impl.mcp_create_server(config={})

    async def test_create_server_other_error_raises_runtimeerror(self):
        c = _FakeRelayClient()
        c.set_response("mcp.config.create", _err("BOOM", "nope"))
        impl, _ = _impl(c)
        with pytest.raises(RuntimeError):
            await impl.mcp_create_server(config={})

    async def test_update_server_success_returns_payload(self):
        c = _FakeRelayClient()
        c.set_response("mcp.config.update", _ok({"updated": True}))
        impl, _ = _impl(c)
        out = await impl.mcp_update_server(server_code="s1", patch={"name": "n"})
        assert out == {"updated": True}
        _, args, kw = _last_call(c)
        assert args[0] == "mcp.config.update"
        assert kw["params"] == {"name": "n", "serverCode": "s1"}

    async def test_update_server_success_non_dict_returns_params(self):
        c = _FakeRelayClient()
        c.set_response("mcp.config.update", _ok("x"))
        impl, _ = _impl(c)
        out = await impl.mcp_update_server(server_code="s1", patch={"name": "n"})
        assert out == {"name": "n", "serverCode": "s1"}

    async def test_update_server_error_raises_runtimeerror(self):
        c = _FakeRelayClient()
        c.set_response("mcp.config.update", _err("BOOM", "nope"))
        impl, _ = _impl(c)
        with pytest.raises(RuntimeError):
            await impl.mcp_update_server(server_code="s1", patch={})

    async def test_delete_server_success_dict_payload_deleted(self):
        c = _FakeRelayClient()
        c.set_response("mcp.config.delete", _ok({"deleted": True}))
        impl, _ = _impl(c)
        assert await impl.mcp_delete_server(server_code="s1") is True

    async def test_delete_server_success_non_dict_payload(self):
        c = _FakeRelayClient()
        c.set_response("mcp.config.delete", _ok(True))
        impl, _ = _impl(c)
        assert await impl.mcp_delete_server(server_code="s1") is True

    async def test_delete_server_error_returns_false(self):
        c = _FakeRelayClient()
        c.set_response("mcp.config.delete", _err("BOOM", "x"))
        impl, _ = _impl(c)
        assert await impl.mcp_delete_server(server_code="s1") is False

    async def test_start_server_success(self):
        c = _FakeRelayClient()
        c.set_response("mcp.server.start", _ok({"started": True}))
        impl, _ = _impl(c)
        out = await impl.mcp_start_server(server_code="s1")
        assert out == {"success": True, "payload": {"started": True}}
        _, args, kw = _last_call(c)
        assert args[0] == "mcp.server.start" and kw["params"] == {"serverCode": "s1"}

    async def test_start_server_error(self):
        c = _FakeRelayClient()
        c.set_response("mcp.server.start", _err("FAIL", "nope"))
        impl, _ = _impl(c)
        out = await impl.mcp_start_server(server_code="s1")
        assert out == {"success": False, "error": {"code": "FAIL", "message": "nope"}}

    async def test_stop_server_success(self):
        c = _FakeRelayClient()
        c.set_response("mcp.server.stop", _ok({"stopped": True}))
        impl, _ = _impl(c)
        out = await impl.mcp_stop_server(server_code="s1")
        assert out["success"] is True
        _, args, _ = _last_call(c)
        assert args[0] == "mcp.server.stop"

    async def test_restart_server_error_no_error_obj(self):
        c = _FakeRelayClient()
        # ResponseFrame ok=False with no error object — exercise the
        # `err.code if err else "UNKNOWN"` defensive branch.
        from engine.community.kernel.frames import ResponseFrame
        c.set_response("mcp.server.restart", ResponseFrame(id="x", ok=False, error=None))
        impl, _ = _impl(c)
        out = await impl.mcp_restart_server(server_code="s1")
        assert out == {"success": False, "error": {"code": "UNKNOWN", "message": "Unknown error"}}

    async def test_get_server_status_dict(self):
        c = _FakeRelayClient()
        c.set_response("mcp.server.status", _ok({"running": True}))
        impl, _ = _impl(c)
        assert await impl.mcp_get_server_status(server_code="s1") == {"running": True}

    async def test_get_server_status_non_dict(self):
        c = _FakeRelayClient()
        c.set_response("mcp.server.status", _ok(["x"]))
        impl, _ = _impl(c)
        assert await impl.mcp_get_server_status(server_code="s1") == {}

    async def test_list_tools_success(self):
        c = _FakeRelayClient()
        c.set_response("mcp.tools.list", _ok({"tools": [{"name": "t1"}, "bad", {"name": "t2"}]}))
        impl, _ = _impl(c)
        assert await impl.mcp_list_tools(server_code="s1") == [{"name": "t1"}, {"name": "t2"}]

    async def test_list_tools_error_returns_empty(self):
        c = _FakeRelayClient()
        c.set_response("mcp.tools.list", _err("X", "y"))
        impl, _ = _impl(c)
        assert await impl.mcp_list_tools(server_code="s1") == []

    async def test_call_tool_success_with_server_code_and_timeout(self):
        c = _FakeRelayClient()
        c.set_response("mcp.tools.call", _ok({"result": "ok"}))
        impl, _ = _impl(c)
        out = await impl.mcp_call_tool(
            server_code="s1", tool_name="t", arguments={"a": 1}, timeout_ms=5000)
        assert out == {"success": True, "payload": {"result": "ok"}}
        _, args, kw = _last_call(c)
        assert args[0] == "mcp.tools.call"
        assert kw["params"] == {"toolName": "t", "arguments": {"a": 1}, "serverCode": "s1"}
        assert kw["timeout"] == 5.0

    async def test_call_tool_success_no_server_code_no_timeout(self):
        c = _FakeRelayClient()
        c.set_response("mcp.tools.call", _ok(None))
        impl, _ = _impl(c)
        out = await impl.mcp_call_tool(server_code="", tool_name="t")
        assert out == {"success": True, "payload": {}}
        _, args, kw = _last_call(c)
        assert kw["params"] == {"toolName": "t", "arguments": {}}
        assert kw["timeout"] == 60.0

    async def test_call_tool_error(self):
        c = _FakeRelayClient()
        c.set_response("mcp.tools.call", _err("TOOL_FAIL", "nope"))
        impl, _ = _impl(c)
        out = await impl.mcp_call_tool(server_code="s1", tool_name="t")
        assert out == {"success": False, "error": {"code": "TOOL_FAIL", "message": "nope"}}

    async def test_list_resources_success(self):
        c = _FakeRelayClient()
        c.set_response("mcp.resources.list", _ok({"resources": [{"uri": "u1"}]}))
        impl, _ = _impl(c)
        assert await impl.mcp_list_resources(server_code="s1") == [{"uri": "u1"}]

    async def test_list_resources_error_returns_empty(self):
        c = _FakeRelayClient()
        c.set_response("mcp.resources.list", _err("X", "y"))
        impl, _ = _impl(c)
        assert await impl.mcp_list_resources(server_code="s1") == []

    async def test_read_resource_dict(self):
        c = _FakeRelayClient()
        c.set_response("mcp.resources.read", _ok({"content": "data"}))
        impl, _ = _impl(c)
        assert await impl.mcp_read_resource(server_code="s1", resource_uri="u1") == {"content": "data"}

    async def test_read_resource_non_dict(self):
        c = _FakeRelayClient()
        c.set_response("mcp.resources.read", _ok("raw"))
        impl, _ = _impl(c)
        assert await impl.mcp_read_resource(server_code="s1", resource_uri="u1") == {}

    async def test_list_prompts_success(self):
        c = _FakeRelayClient()
        c.set_response("mcp.prompts.list", _ok({"prompts": [{"name": "p1"}]}))
        impl, _ = _impl(c)
        assert await impl.mcp_list_prompts(server_code="s1") == [{"name": "p1"}]

    async def test_list_prompts_error_returns_empty(self):
        c = _FakeRelayClient()
        c.set_response("mcp.prompts.list", _err("X", "y"))
        impl, _ = _impl(c)
        assert await impl.mcp_list_prompts(server_code="s1") == []

    async def test_get_prompt_success(self):
        c = _FakeRelayClient()
        c.set_response("mcp.prompts.get", _ok({"messages": []}))
        impl, _ = _impl(c)
        out = await impl.mcp_get_prompt(server_code="s1", prompt_name="p", arguments={"x": 1})
        assert out == {"success": True, "payload": {"messages": []}}
        _, args, kw = _last_call(c)
        assert args[0] == "mcp.prompts.get"
        assert kw["params"] == {"serverCode": "s1", "name": "p", "arguments": {"x": 1}}

    async def test_get_prompt_error(self):
        c = _FakeRelayClient()
        c.set_response("mcp.prompts.get", _err("PROMPT_FAIL", "nope"))
        impl, _ = _impl(c)
        out = await impl.mcp_get_prompt(server_code="s1", prompt_name="p")
        assert out == {"success": False, "error": {"code": "PROMPT_FAIL", "message": "nope"}}

    async def test_filter_servers_no_query_returns_all(self):
        c = _FakeRelayClient()
        c.set_response("mcp.config.list", _ok({"servers": [{"serverCode": "s1", "name": "Alpha"}]}))
        impl, _ = _impl(c)
        out = await impl.mcp_filter_servers(query=None)
        assert out == [{"serverCode": "s1", "name": "Alpha"}]

    async def test_filter_servers_with_query_matches_servercode(self):
        c = _FakeRelayClient()
        c.set_response("mcp.config.list", _ok({"servers": [
            {"serverCode": "s1", "name": "Alpha"},
            {"serverCode": "s2", "name": "Beta"},
        ]}))
        impl, _ = _impl(c)
        out = await impl.mcp_filter_servers(query="S1")
        assert out == [{"serverCode": "s1", "name": "Alpha"}]

    async def test_filter_servers_with_query_matches_name(self):
        c = _FakeRelayClient()
        c.set_response("mcp.config.list", _ok({"servers": [
            {"serverCode": "s1", "name": "Alpha"},
            {"serverCode": "s2", "name": "Beta"},
        ]}))
        impl, _ = _impl(c)
        out = await impl.mcp_filter_servers(query="beta")
        assert out == [{"serverCode": "s2", "name": "Beta"}]

    async def test_apply_server_filter_success(self):
        c = _FakeRelayClient()
        c.set_response("mcp.filter_servers", _ok({"applied": True}))
        impl, _ = _impl(c)
        out = await impl.mcp_apply_server_filter(server_codes=["s1", "s2"], timeout_seconds=60)
        assert out == {"applied": True}
        _, args, kw = _last_call(c)
        assert args[0] == "mcp.filter_servers"
        assert kw["params"] == {"serverCodes": ["s1", "s2"], "timeoutSeconds": 60}

    async def test_apply_server_filter_error_raises(self):
        c = _FakeRelayClient()
        c.set_response("mcp.filter_servers", _err("FILTER_FAIL", "nope"))
        impl, _ = _impl(c)
        with pytest.raises(RuntimeError):
            await impl.mcp_apply_server_filter(server_codes=[])

    async def test_apply_server_filter_non_dict_payload(self):
        c = _FakeRelayClient()
        c.set_response("mcp.filter_servers", _ok("raw"))
        impl, _ = _impl(c)
        assert await impl.mcp_apply_server_filter(server_codes=[]) == {}


# ── _cron.py ─────────────────────────────────────────────────────────────────


class TestCronMixin:
    async def test_list_jobs_success_dict_jobs(self):
        c = _FakeRelayClient()
        c.set_response("cron.list", _ok({"jobs": [{"id": "j1"}, "x", {"id": "j2"}]}))
        impl, _ = _impl(c)
        assert await impl.cron_list_jobs() == [{"id": "j1"}, {"id": "j2"}]
        _, args, kw = _last_call(c)
        assert args[0] == "cron.list" and kw["params"] == {"includeDisabled": True}

    async def test_list_jobs_success_dict_entries_key(self):
        c = _FakeRelayClient()
        c.set_response("cron.list", _ok({"entries": [{"id": "j1"}]}))
        impl, _ = _impl(c)
        assert await impl.cron_list_jobs() == [{"id": "j1"}]

    async def test_list_jobs_success_list_payload(self):
        c = _FakeRelayClient()
        c.set_response("cron.list", _ok([{"id": "j1"}]))
        impl, _ = _impl(c)
        assert await impl.cron_list_jobs() == [{"id": "j1"}]

    async def test_list_jobs_error_returns_empty(self):
        c = _FakeRelayClient()
        c.set_response("cron.list", _err("X", "y"))
        impl, _ = _impl(c)
        assert await impl.cron_list_jobs() == []

    async def test_get_job_success(self):
        c = _FakeRelayClient()
        c.set_response("cron.get", _ok({"id": "j1", "name": "n"}))
        impl, _ = _impl(c)
        assert await impl.cron_get_job(job_id="j1") == {"id": "j1", "name": "n"}

    async def test_get_job_fallback_scan(self):
        c = _FakeRelayClient()
        c.set_response("cron.get", _err("NOT_FOUND", "x"))
        c.set_response("cron.list", _ok({"jobs": [{"id": "j1"}, {"id": "j2"}]}))
        impl, _ = _impl(c)
        assert await impl.cron_get_job(job_id="j2") == {"id": "j2"}

    async def test_get_job_fallback_not_found(self):
        c = _FakeRelayClient()
        c.set_response("cron.get", _err("NOT_FOUND", "x"))
        c.set_response("cron.list", _ok({"jobs": [{"id": "j1"}]}))
        impl, _ = _impl(c)
        assert await impl.cron_get_job(job_id="missing") is None

    async def test_get_job_non_dict_payload_fallback(self):
        c = _FakeRelayClient()
        c.set_response("cron.get", _ok(["not", "dict"]))
        c.set_response("cron.list", _ok({"jobs": [{"id": "j1"}]}))
        impl, _ = _impl(c)
        assert await impl.cron_get_job(job_id="j1") == {"id": "j1"}

    async def test_get_status_success(self):
        c = _FakeRelayClient()
        c.set_response("cron.status", _ok({"running": []}))
        impl, _ = _impl(c)
        assert await impl.cron_get_status() == {"running": []}

    async def test_get_status_error_returns_empty(self):
        c = _FakeRelayClient()
        c.set_response("cron.status", _err("X", "y"))
        impl, _ = _impl(c)
        assert await impl.cron_get_status() == {}

    async def test_get_status_non_dict(self):
        c = _FakeRelayClient()
        c.set_response("cron.status", _ok(["x"]))
        impl, _ = _impl(c)
        assert await impl.cron_get_status() == {}

    async def test_get_runs_success_dict_entries(self):
        c = _FakeRelayClient()
        c.set_response("cron.runs", _ok({"entries": [{"id": "r1"}]}))
        impl, _ = _impl(c)
        assert await impl.cron_get_runs(job_id="j1", limit=5) == [{"id": "r1"}]
        _, args, kw = _last_call(c)
        assert args[0] == "cron.runs" and kw["params"] == {"id": "j1", "limit": 5}

    async def test_get_runs_success_list(self):
        c = _FakeRelayClient()
        c.set_response("cron.runs", _ok([{"id": "r1"}]))
        impl, _ = _impl(c)
        assert await impl.cron_get_runs(job_id="j1") == [{"id": "r1"}]

    async def test_get_runs_error_returns_empty(self):
        c = _FakeRelayClient()
        c.set_response("cron.runs", _err("X", "y"))
        impl, _ = _impl(c)
        assert await impl.cron_get_runs(job_id="j1") == []

    async def test_get_running_jobs_from_status_list(self):
        c = _FakeRelayClient()
        c.set_response("cron.status", _ok({"running": [{"id": "j1"}, "x", {"id": "j2"}]}))
        impl, _ = _impl(c)
        assert await impl.cron_get_running_jobs() == [{"id": "j1"}, {"id": "j2"}]

    async def test_get_running_jobs_fallback_filter_state(self):
        c = _FakeRelayClient()
        c.set_response("cron.status", _ok({}))  # no running list
        c.set_response("cron.list", _ok({"jobs": [{"id": "j1", "state": "running"},
                                                   {"id": "j2", "state": "idle"}]}))
        impl, _ = _impl(c)
        assert await impl.cron_get_running_jobs() == [{"id": "j1", "state": "running"}]

    async def test_add_job_success(self):
        c = _FakeRelayClient()
        c.set_response("cron.add", _ok({"id": "j1", "created": True}))
        impl, _ = _impl(c)
        out = await impl.cron_add_job(job={"name": "n"})
        assert out == {"id": "j1", "created": True}

    async def test_add_job_error_raises(self):
        c = _FakeRelayClient()
        c.set_response("cron.add", _err("ADD_FAIL", "nope"))
        impl, _ = _impl(c)
        with pytest.raises(RuntimeError):
            await impl.cron_add_job(job={})

    async def test_add_job_non_dict_payload_raises(self):
        c = _FakeRelayClient()
        c.set_response("cron.add", _ok(["not", "dict"]))
        impl, _ = _impl(c)
        with pytest.raises(RuntimeError):
            await impl.cron_add_job(job={})

    async def test_update_job_success(self):
        c = _FakeRelayClient()
        c.set_response("cron.update", _ok({"id": "j1", "updated": True}))
        impl, _ = _impl(c)
        out = await impl.cron_update_job(job_id="j1", patch={"name": "n"})
        assert out == {"id": "j1", "updated": True}
        _, args, kw = _last_call(c)
        assert args[0] == "cron.update"
        assert kw["params"] == {"id": "j1", "patch": {"name": "n"}}

    async def test_update_job_error_raises(self):
        c = _FakeRelayClient()
        c.set_response("cron.update", _err("X", "y"))
        impl, _ = _impl(c)
        with pytest.raises(RuntimeError):
            await impl.cron_update_job(job_id="j1", patch={})

    async def test_update_job_non_dict_raises(self):
        c = _FakeRelayClient()
        c.set_response("cron.update", _ok("raw"))
        impl, _ = _impl(c)
        with pytest.raises(RuntimeError):
            await impl.cron_update_job(job_id="j1", patch={})

    async def test_remove_job_success_dict_removed(self):
        c = _FakeRelayClient()
        c.set_response("cron.remove", _ok({"removed": True}))
        impl, _ = _impl(c)
        assert await impl.cron_remove_job(job_id="j1") is True

    async def test_remove_job_success_dict_ok_key(self):
        c = _FakeRelayClient()
        c.set_response("cron.remove", _ok({"ok": True}))
        impl, _ = _impl(c)
        assert await impl.cron_remove_job(job_id="j1") is True

    async def test_remove_job_success_non_dict(self):
        c = _FakeRelayClient()
        c.set_response("cron.remove", _ok(True))
        impl, _ = _impl(c)
        assert await impl.cron_remove_job(job_id="j1") is True

    async def test_remove_job_error_returns_false(self):
        c = _FakeRelayClient()
        c.set_response("cron.remove", _err("X", "y"))
        impl, _ = _impl(c)
        assert await impl.cron_remove_job(job_id="j1") is False

    async def test_run_job_success(self):
        c = _FakeRelayClient()
        c.set_response("cron.run", _ok({"ran": "j1"}))
        impl, _ = _impl(c)
        out = await impl.cron_run_job(job_id="j1")
        assert out == {"success": True, "payload": {"ran": "j1"}}
        _, args, kw = _last_call(c)
        assert args[0] == "cron.run" and kw["params"] == {"id": "j1", "mode": "force"}

    async def test_run_job_success_empty_payload_synthesizes(self):
        c = _FakeRelayClient()
        c.set_response("cron.run", _ok(None))
        impl, _ = _impl(c)
        out = await impl.cron_run_job(job_id="j1")
        assert out == {"success": True, "payload": {"ran": "j1"}}

    async def test_run_job_error(self):
        c = _FakeRelayClient()
        c.set_response("cron.run", _err("RUN_FAIL", "nope"))
        impl, _ = _impl(c)
        out = await impl.cron_run_job(job_id="j1")
        assert out == {"success": False, "error": {"code": "RUN_FAIL", "message": "nope"}}


# ── _file.py ─────────────────────────────────────────────────────────────────


class TestFileMixin:
    async def test_upload_success_with_content(self):
        c = _FakeRelayClient()
        c.set_response("file.upload", _ok({"path": "/x", "saved": True}))
        impl, _ = _impl(c)
        out = await impl.file_upload(path="/x", content_bytes=b"data")
        assert out == {"path": "/x", "saved": True}
        _, args, kw = _last_call(c)
        assert args[0] == "file.upload"
        assert kw["params"] == {"path": "/x", "content": b"data"}

    async def test_upload_success_no_content(self):
        c = _FakeRelayClient()
        c.set_response("file.upload", _ok(None))
        impl, _ = _impl(c)
        out = await impl.file_upload(path="/x")
        assert out == {"path": "/x"}
        _, args, kw = _last_call(c)
        assert kw["params"] == {"path": "/x"}

    async def test_upload_success_non_dict_payload(self):
        c = _FakeRelayClient()
        c.set_response("file.upload", _ok("raw"))
        impl, _ = _impl(c)
        assert await impl.file_upload(path="/x") == {"path": "/x"}

    async def test_upload_error_raises(self):
        c = _FakeRelayClient()
        c.set_response("file.upload", _err("UPLOAD_FAIL", "nope"))
        impl, _ = _impl(c)
        with pytest.raises(RuntimeError):
            await impl.file_upload(path="/x")

    async def test_read_success(self):
        c = _FakeRelayClient()
        c.set_response("file.read", _ok({"content": "data"}))
        impl, _ = _impl(c)
        assert await impl.file_read(path="/x") == {"content": "data"}

    async def test_read_success_non_dict(self):
        c = _FakeRelayClient()
        c.set_response("file.read", _ok("raw"))
        impl, _ = _impl(c)
        assert await impl.file_read(path="/x") == {"path": "/x"}

    async def test_read_error_raises_filenotfounderror(self):
        c = _FakeRelayClient()
        c.set_response("file.read", _err("NOT_FOUND", "nope"))
        impl, _ = _impl(c)
        with pytest.raises(FileNotFoundError):
            await impl.file_read(path="/x")

    async def test_remove_returns_bool(self):
        c = _FakeRelayClient()
        c.set_response("file.remove", _ok({}))
        impl, _ = _impl(c)
        assert await impl.file_remove(path="/x") is True

    async def test_remove_error_returns_false(self):
        c = _FakeRelayClient()
        c.set_response("file.remove", _err("X", "y"))
        impl, _ = _impl(c)
        assert await impl.file_remove(path="/x") is False

    async def test_rmtree_returns_bool(self):
        c = _FakeRelayClient()
        c.set_response("file.rmtree", _ok({}))
        impl, _ = _impl(c)
        assert await impl.file_rmtree(path="/x") is True

    async def test_rmtree_error_returns_false(self):
        c = _FakeRelayClient()
        c.set_response("file.rmtree", _err("X", "y"))
        impl, _ = _impl(c)
        assert await impl.file_rmtree(path="/x") is False

    async def test_list_dir_success(self):
        c = _FakeRelayClient()
        c.set_response("file.list", _ok({"entries": [{"name": "a"}, "x", {"name": "b"}]}))
        impl, _ = _impl(c)
        assert await impl.file_list_dir(path="/x") == [{"name": "a"}, {"name": "b"}]

    async def test_list_dir_success_list_payload(self):
        c = _FakeRelayClient()
        c.set_response("file.list", _ok([{"name": "a"}]))
        impl, _ = _impl(c)
        assert await impl.file_list_dir(path="/x") == [{"name": "a"}]

    async def test_list_dir_error_returns_empty(self):
        c = _FakeRelayClient()
        c.set_response("file.list", _err("X", "y"))
        impl, _ = _impl(c)
        assert await impl.file_list_dir(path="/x") == []


# ── _skills.py ───────────────────────────────────────────────────────────────


class TestSkillsMixin:
    async def test_list_success(self):
        c = _FakeRelayClient()
        c.set_response("skills.list", _ok({"skills": [{"skillId": "x"}, "bad", {"skillId": "y"}]}))
        impl, _ = _impl(c)
        assert await impl.skills_list() == [{"skillId": "x"}, {"skillId": "y"}]

    async def test_list_error_returns_empty(self):
        c = _FakeRelayClient()
        c.set_response("skills.list", _err("X", "y"))
        impl, _ = _impl(c)
        assert await impl.skills_list() == []

    async def test_get_success(self):
        c = _FakeRelayClient()
        c.set_response("skills.get", _ok({"skillId": "x", "name": "n"}))
        impl, _ = _impl(c)
        assert await impl.skills_get(skill_id="x") == {"skillId": "x", "name": "n"}

    async def test_get_error_returns_none(self):
        c = _FakeRelayClient()
        c.set_response("skills.get", _err("X", "y"))
        impl, _ = _impl(c)
        assert await impl.skills_get(skill_id="x") is None

    async def test_get_empty_payload_returns_none(self):
        c = _FakeRelayClient()
        c.set_response("skills.get", _ok(None))
        impl, _ = _impl(c)
        assert await impl.skills_get(skill_id="x") is None

    async def test_get_non_dict_payload_returns_none(self):
        c = _FakeRelayClient()
        c.set_response("skills.get", _ok(["x"]))
        impl, _ = _impl(c)
        assert await impl.skills_get(skill_id="x") is None

    async def test_install_success_returns_payload(self):
        cfg = {"skillId": "x"}
        c = _FakeRelayClient()
        c.set_response("skills.install", _ok({"skillId": "x", "installed": True}))
        impl, _ = _impl(c)
        assert await impl.skills_install(config=cfg) == {"skillId": "x", "installed": True}

    async def test_install_success_non_dict_returns_config(self):
        cfg = {"skillId": "x"}
        c = _FakeRelayClient()
        c.set_response("skills.install", _ok("raw"))
        impl, _ = _impl(c)
        assert await impl.skills_install(config=cfg) is cfg

    async def test_install_error_raises(self):
        c = _FakeRelayClient()
        c.set_response("skills.install", _err("X", "y"))
        impl, _ = _impl(c)
        with pytest.raises(RuntimeError):
            await impl.skills_install(config={})

    async def test_uninstall_success_dict_removed(self):
        c = _FakeRelayClient()
        c.set_response("skills.uninstall", _ok({"removed": True}))
        impl, _ = _impl(c)
        assert await impl.skills_uninstall(skill_id="x") is True

    async def test_uninstall_success_non_dict(self):
        c = _FakeRelayClient()
        c.set_response("skills.uninstall", _ok(True))
        impl, _ = _impl(c)
        assert await impl.skills_uninstall(skill_id="x") is True

    async def test_uninstall_error_returns_false(self):
        c = _FakeRelayClient()
        c.set_response("skills.uninstall", _err("X", "y"))
        impl, _ = _impl(c)
        assert await impl.skills_uninstall(skill_id="x") is False

    async def test_update_success_returns_payload(self):
        c = _FakeRelayClient()
        c.set_response("skills.update", _ok({"updated": True}))
        impl, _ = _impl(c)
        out = await impl.skills_update(skill_id="x", patch={"name": "n"})
        assert out == {"updated": True}
        _, args, kw = _last_call(c)
        assert args[0] == "skills.update"
        assert kw["params"] == {"name": "n", "skillId": "x"}

    async def test_update_success_non_dict_returns_params(self):
        c = _FakeRelayClient()
        c.set_response("skills.update", _ok("raw"))
        impl, _ = _impl(c)
        out = await impl.skills_update(skill_id="x", patch={"name": "n"})
        assert out == {"name": "n", "skillId": "x"}

    async def test_update_error_raises(self):
        c = _FakeRelayClient()
        c.set_response("skills.update", _err("X", "y"))
        impl, _ = _impl(c)
        with pytest.raises(RuntimeError):
            await impl.skills_update(skill_id="x", patch={})

    async def test_enable_returns_bool(self):
        c = _FakeRelayClient()
        c.set_response("skills.update", _ok({}))
        impl, _ = _impl(c)
        assert await impl.skills_enable(skill_id="x") is True
        _, args, kw = _last_call(c)
        assert kw["params"] == {"skillId": "x", "enabled": True}

    async def test_enable_error_returns_false(self):
        c = _FakeRelayClient()
        c.set_response("skills.update", _err("X", "y"))
        impl, _ = _impl(c)
        assert await impl.skills_enable(skill_id="x") is False

    async def test_disable_returns_bool(self):
        c = _FakeRelayClient()
        c.set_response("skills.update", _ok({}))
        impl, _ = _impl(c)
        assert await impl.skills_disable(skill_id="x") is True
        _, args, kw = _last_call(c)
        assert kw["params"] == {"skillId": "x", "enabled": False}

    async def test_execute_success(self):
        c = _FakeRelayClient()
        c.set_response("skills.execute", _ok({"result": "ok"}))
        impl, _ = _impl(c)
        out = await impl.skills_execute(skill_id="x", args={"a": 1})
        assert out == {"success": True, "payload": {"result": "ok"}}
        _, args, kw = _last_call(c)
        assert args[0] == "skills.execute"
        assert kw["params"] == {"skillId": "x", "args": {"a": 1}}

    async def test_execute_error(self):
        c = _FakeRelayClient()
        c.set_response("skills.execute", _err("EXEC_FAIL", "nope"))
        impl, _ = _impl(c)
        out = await impl.skills_execute(skill_id="x")
        assert out == {"success": False, "error": {"code": "EXEC_FAIL", "message": "nope"}}

    async def test_validate_success_dict(self):
        c = _FakeRelayClient()
        c.set_response("skills.validate", _ok({"valid": True}))
        impl, _ = _impl(c)
        assert await impl.skills_validate(config={"x": 1}) == {"valid": True}

    async def test_validate_success_non_dict(self):
        c = _FakeRelayClient()
        c.set_response("skills.validate", _ok("raw"))
        impl, _ = _impl(c)
        assert await impl.skills_validate(config={}) == {}

    async def test_validate_error(self):
        c = _FakeRelayClient()
        c.set_response("skills.validate", _err("VALIDATE_FAIL", "nope"))
        impl, _ = _impl(c)
        out = await impl.skills_validate(config={})
        assert out == {"success": False, "error": {"code": "VALIDATE_FAIL", "message": "nope"}}

    async def test_discover_success(self):
        c = _FakeRelayClient()
        c.set_response("skills.discover", _ok({"skills": [{"skillId": "x"}]}))
        impl, _ = _impl(c)
        assert await impl.skills_discover(source="market") == [{"skillId": "x"}]
        _, args, kw = _last_call(c)
        assert args[0] == "skills.discover" and kw["params"] == {"source": "market"}

    async def test_discover_error_returns_empty(self):
        c = _FakeRelayClient()
        c.set_response("skills.discover", _err("X", "y"))
        impl, _ = _impl(c)
        assert await impl.skills_discover(source="market") == []

    async def test_sync_symlinks_success_payload(self):
        c = _FakeRelayClient()
        c.set_response("skills.sync_symlinks", _ok({"synced": 3}))
        impl, _ = _impl(c)
        assert await impl.skills_sync_symlinks() == {"synced": 3}

    async def test_sync_symlinks_success_non_dict(self):
        c = _FakeRelayClient()
        c.set_response("skills.sync_symlinks", _ok("raw"))
        impl, _ = _impl(c)
        assert await impl.skills_sync_symlinks() == {"success": True}

    async def test_sync_symlinks_error(self):
        c = _FakeRelayClient()
        c.set_response("skills.sync_symlinks", _err("SYNC_FAIL", "nope"))
        impl, _ = _impl(c)
        out = await impl.skills_sync_symlinks()
        assert out == {"success": False, "error": {"code": "SYNC_FAIL", "message": "nope"}}

    async def test_sync_bindpaths_success(self):
        c = _FakeRelayClient()
        c.set_response("skills.sync_bindpaths", _ok({"ok": True}))
        impl, _ = _impl(c)
        assert await impl.skills_sync_bindpaths() == {"ok": True}

    async def test_clean_symlinks_success(self):
        c = _FakeRelayClient()
        c.set_response("skills.clean_symlinks", _ok({"cleaned": 1}))
        impl, _ = _impl(c)
        assert await impl.skills_clean_symlinks() == {"cleaned": 1}

    async def test_ensure_center_success(self):
        c = _FakeRelayClient()
        c.set_response("skills.ensure_center", _ok({"ensured": True}))
        impl, _ = _impl(c)
        assert await impl.skills_ensure_center() == {"ensured": True}


# ── _session.py ──────────────────────────────────────────────────────────────


class TestSessionMixin:
    async def test_list_success_dict_sessions(self):
        c = _FakeRelayClient()
        c.set_response("sessions.list", _ok({"sessions": [{"key": "a"}, "x", {"key": "b"}]}))
        impl, _ = _impl(c)
        out = await impl.sessions_list(token=None, offset=0, limit=50, agent_id=None)
        assert [s["key"] for s in out] == ["a", "b"]

    async def test_list_success_list_payload(self):
        c = _FakeRelayClient()
        c.set_response("sessions.list", _ok([{"key": "a"}]))
        impl, _ = _impl(c)
        assert await impl.sessions_list() == [{"key": "a"}]

    async def test_list_non_dict_non_list_returns_empty(self):
        c = _FakeRelayClient()
        c.set_response("sessions.list", _ok("raw"))
        impl, _ = _impl(c)
        assert await impl.sessions_list() == []

    async def test_list_error_returns_empty(self):
        c = _FakeRelayClient()
        c.set_response("sessions.list", _err("X", "y"))
        impl, _ = _impl(c)
        assert await impl.sessions_list() == []

    async def test_list_rpc_exception_returns_empty(self):
        c = _FakeRelayClient()
        c.set_response("sessions.list", _ok({"sessions": []}))
        c.set_raise(ConnectionError("boom"))
        impl, _ = _impl(c)
        assert await impl.sessions_list() == []

    async def test_list_agent_id_filter(self):
        c = _FakeRelayClient()
        c.set_response("sessions.list", _ok({"sessions": [
            {"key": "a", "agentId": "g1"},
            {"key": "b", "agentId": "g2"},
            {"key": "c", "agentId": "g1"},
        ]}))
        impl, _ = _impl(c)
        out = await impl.sessions_list(agent_id="g1")
        assert [s["key"] for s in out] == ["a", "c"]

    async def test_list_session_key_filters_before_pagination(self):
        c = _FakeRelayClient()
        c.set_response("sessions.list", _ok({"sessions": [
            {"key": "first", "agentId": "g1"},
            {"key": "target", "agentId": "g1"},
            {"key": "other", "agentId": "g1"},
        ]}))
        impl, _ = _impl(c)

        out = await impl.sessions_list(
            agent_id="g1", session_key="target", offset=0, limit=1,
        )

        assert out == [{"key": "target", "agentId": "g1"}]

    async def test_list_blank_session_key_preserves_existing_pagination(self):
        c = _FakeRelayClient()
        c.set_response("sessions.list", _ok({"sessions": [
            {"key": "first"}, {"key": "second"},
        ]}))
        impl, _ = _impl(c)

        assert await impl.sessions_list(session_key="   ", offset=1, limit=1) == [
            {"key": "second"},
        ]

    async def test_list_offset_limit(self):
        c = _FakeRelayClient()
        c.set_response("sessions.list", _ok({"sessions": [{"key": str(i)} for i in range(10)]}))
        impl, _ = _impl(c)
        out = await impl.sessions_list(offset=2, limit=3)
        assert [s["key"] for s in out] == ["2", "3", "4"]

    async def test_create_success_full_params(self):
        c = _FakeRelayClient()
        c.set_response("sessions.patch", _ok({"key": "k"}))
        impl, _ = _impl(c)
        await impl.session_create(key="k", label="L", model="m", cwd="/x")
        _, args, kw = _last_call(c)
        assert args[0] == "sessions.patch"
        assert kw["params"] == {"key": "k", "permissionMode": "bypassPermissions",
                                 "label": "L", "model": "m", "cwd": "/x"}
        assert kw["timeout"] == 60.0

    async def test_create_success_minimal_params(self):
        c = _FakeRelayClient()
        c.set_response("sessions.patch", _ok(None))
        impl, _ = _impl(c)
        out = await impl.session_create(key="k")
        assert out == {"key": "k"}
        _, args, kw = _last_call(c)
        assert kw["params"] == {"key": "k", "permissionMode": "bypassPermissions"}

    async def test_create_error_raises(self):
        c = _FakeRelayClient()
        c.set_response("sessions.patch", _err("CREATE_FAIL", "nope"))
        impl, _ = _impl(c)
        with pytest.raises(RuntimeError):
            await impl.session_create(key="k")

    async def test_delete_success(self):
        c = _FakeRelayClient()
        c.set_response("sessions.delete", _ok({}))
        impl, _ = _impl(c)
        assert await impl.session_delete(key="k") is True
        _, args, kw = _last_call(c)
        assert kw["params"] == {"key": "k", "force": True}

    async def test_delete_error_returns_false(self):
        c = _FakeRelayClient()
        c.set_response("sessions.delete", _err("X", "y"))
        impl, _ = _impl(c)
        assert await impl.session_delete(key="k") is False

    async def test_delete_rpc_exception_returns_false(self):
        c = _FakeRelayClient()
        c.set_raise(ConnectionError("boom"))
        impl, _ = _impl(c)
        assert await impl.session_delete(key="k") is False

    async def test_reset_success(self):
        c = _FakeRelayClient()
        c.set_response("sessions.reset", _ok({"reset": True}))
        impl, _ = _impl(c)
        out = await impl.session_reset(key="k")
        assert out == {"success": True, "payload": {"reset": True}}
        _, args, kw = _last_call(c)
        assert args[0] == "sessions.reset"
        assert kw["params"] == {"sessionKey": "k"}
        assert kw["timeout"] == 15.0

    async def test_reset_error_in_band(self):
        c = _FakeRelayClient()
        c.set_response("sessions.reset", _err("RESET_FAIL", "nope"))
        impl, _ = _impl(c)
        out = await impl.session_reset(key="k")
        assert out == {"success": False, "error": {"code": "RESET_FAIL", "message": "nope"}}

    async def test_clear_uses_key_param(self):
        c = _FakeRelayClient()
        c.set_response("sessions.reset", _ok({}))
        impl, _ = _impl(c)
        await impl.session_clear(key="k")
        _, args, kw = _last_call(c)
        assert kw["params"] == {"key": "k"}

    async def test_reset_rpc_exception_in_band(self):
        c = _FakeRelayClient()
        c.set_raise(ConnectionError("boom"))
        impl, _ = _impl(c)
        out = await impl.session_reset(key="k")
        assert out["success"] is False
        assert out["error"]["code"] == "INTERNAL_ERROR"

    async def test_reset_connect_exception_in_band(self):
        impl, _ = _impl(_FakeRelayClient())
        async def _bad_relay():
            raise ConnectionError("no relay")
        impl._relay = _bad_relay
        out = await impl.session_reset(key="k")
        assert out["success"] is False
        assert out["error"]["code"] == "INTERNAL_ERROR"

    async def test_get_history_success_dict(self):
        c = _FakeRelayClient()
        c.set_response("chat.history", _ok({"messages": [{"role": "user"}, "x", {"role": "assistant"}]}))
        impl, _ = _impl(c)
        out = await impl.session_get_history(key="k", limit=10)
        assert len(out) == 2
        _, args, kw = _last_call(c)
        assert args[0] == "chat.history"
        assert kw["params"] == {"sessionKey": "k", "limit": 10}

    async def test_get_history_success_list(self):
        c = _FakeRelayClient()
        c.set_response("chat.history", _ok([{"role": "user"}]))
        impl, _ = _impl(c)
        assert await impl.session_get_history(key="k") == [{"role": "user"}]

    async def test_get_history_non_dict_non_list(self):
        c = _FakeRelayClient()
        c.set_response("chat.history", _ok("raw"))
        impl, _ = _impl(c)
        assert await impl.session_get_history(key="k") == []

    async def test_get_history_error_returns_empty(self):
        c = _FakeRelayClient()
        c.set_response("chat.history", _err("X", "y"))
        impl, _ = _impl(c)
        assert await impl.session_get_history(key="k") == []

    async def test_get_history_rpc_exception_returns_empty(self):
        c = _FakeRelayClient()
        c.set_raise(ConnectionError("boom"))
        impl, _ = _impl(c)
        assert await impl.session_get_history(key="k") == []


# ── _chat.py ─────────────────────────────────────────────────────────────────


class TestChatMixin:
    async def test_stream_toplevel_event_hint(self):
        c = _FakeRelayClient()
        c._events = [
            {"_source_event": "exec.approval.requested", "runId": "r1"},
            {"state": "final", "runId": "r1"},
        ]
        impl, _ = _impl(c)
        frames = [f async for f in impl.chat_stream(session_key="s", message="m")]
        assert frames[0].event == "exec.approval.requested"
        # hint popped from payload
        assert "_source_event" not in frames[0].payload

    async def test_stream_event_hint(self):
        c = _FakeRelayClient()
        c._events = [
            {"event": "chat", "runId": "r1"},
            {"state": "final", "runId": "r1"},
        ]
        impl, _ = _impl(c)
        frames = [f async for f in impl.chat_stream(session_key="s", message="m")]
        assert frames[0].event == "chat"
        assert "event" not in frames[0].payload

    async def test_stream_state_error_event_name(self):
        c = _FakeRelayClient()
        c._events = [
            {"state": "error", "runId": "r1"},
        ]
        impl, _ = _impl(c)
        frames = [f async for f in impl.chat_stream(session_key="s", message="m")]
        assert frames[0].event == "error"
        assert len(frames) == 1  # error terminates

    async def test_stream_state_aborted_event_name(self):
        c = _FakeRelayClient()
        c._events = [
            {"state": "aborted", "runId": "r1"},
        ]
        impl, _ = _impl(c)
        frames = [f async for f in impl.chat_stream(session_key="s", message="m")]
        assert frames[0].event == "aborted"
        assert len(frames) == 1

    async def test_stream_default_agent_event(self):
        c = _FakeRelayClient()
        c._events = [
            {"state": "delta", "runId": "r1"},
            {"state": "final", "runId": "r1"},
        ]
        impl, _ = _impl(c)
        frames = [f async for f in impl.chat_stream(session_key="s", message="m")]
        assert frames[0].event == "agent"

    async def test_stream_explicit_non_toplevel_event(self):
        c = _FakeRelayClient()
        c._events = [
            {"event": "custom_channel", "runId": "r1"},
            {"state": "final", "runId": "r1"},
        ]
        impl, _ = _impl(c)
        frames = [f async for f in impl.chat_stream(session_key="s", message="m")]
        assert frames[0].event == "custom_channel"

    async def test_chat_abort_connect_exception(self):
        impl, _ = _impl(_FakeRelayClient())
        # Force _relay() to raise by replacing the client with one that fails connect.
        # Easier: monkeypatch _relay on the impl instance.
        async def _bad_relay():
            raise ConnectionError("no relay")
        impl._relay = _bad_relay
        out = await impl.chat_abort(session_key="s", run_id="r1")
        assert out["success"] is False
        assert out["error"]["code"] == "INTERNAL_ERROR"

    async def test_chat_abort_rpc_exception(self):
        c = _FakeRelayClient()
        c.set_raise(ConnectionError("boom"))
        impl, _ = _impl(c)
        out = await impl.chat_abort(session_key="s", run_id="r1")
        assert out["success"] is False
        assert out["error"]["code"] == "INTERNAL_ERROR"

    async def test_chat_abort_without_run_id(self):
        c = _FakeRelayClient()
        c.set_response("chat.abort", _ok({"aborted": True}))
        impl, _ = _impl(c)
        out = await impl.chat_abort(session_key="s")
        assert out["success"] is True
        _, args, kw = _last_call(c)
        assert kw["params"] == {"sessionKey": "s"}

    async def test_chat_inject_connect_exception(self):
        impl, _ = _impl(_FakeRelayClient())
        async def _bad_relay():
            raise ConnectionError("no relay")
        impl._relay = _bad_relay
        out = await impl.chat_inject(session_key="s", message="hi")
        assert out["success"] is False
        assert out["error"]["code"] == "INTERNAL_ERROR"

    async def test_chat_inject_rpc_exception(self):
        c = _FakeRelayClient()
        c.set_raise(ConnectionError("boom"))
        impl, _ = _impl(c)
        out = await impl.chat_inject(session_key="s", message="hi")
        assert out["success"] is False
        assert out["error"]["code"] == "INTERNAL_ERROR"

    async def test_chat_inject_with_label(self):
        c = _FakeRelayClient()
        c.set_response("chat.inject", _ok({"ok": True}))
        impl, _ = _impl(c)
        out = await impl.chat_inject(session_key="s", message="hi", label="L")
        assert out["success"] is True
        _, args, kw = _last_call(c)
        assert kw["params"] == {"sessionKey": "s", "message": "hi", "label": "L"}

    async def test_chat_inject_without_label(self):
        c = _FakeRelayClient()
        c.set_response("chat.inject", _ok({"ok": True}))
        impl, _ = _impl(c)
        await impl.chat_inject(session_key="s", message="hi")
        _, args, kw = _last_call(c)
        assert kw["params"] == {"sessionKey": "s", "message": "hi"}

    async def test_resolve_exec_approval_with_message(self):
        c = _FakeRelayClient()
        c.set_response("interaction.resolve", (_ok({}), []))
        impl, _ = _impl(c)
        out = await impl.resolve_exec_approval(
            session_key="s", run_id="r1", decision="allow-once", message="ok")
        assert out["success"] is True
        assert out["followup_events"] == []
        _, args, kw = _last_call(c)
        assert kw["params"] == {"interactionId": "r1", "decision": "allow-once", "message": "ok"}

    async def test_resolve_exec_approval_without_message(self):
        c = _FakeRelayClient()
        c.set_response("interaction.resolve", (_ok({}), []))
        impl, _ = _impl(c)
        await impl.resolve_exec_approval(session_key="s", run_id="r1", decision="deny")
        _, args, kw = _last_call(c)
        assert kw["params"] == {"interactionId": "r1", "decision": "deny"}

    async def test_resolve_interaction_with_response(self):
        c = _FakeRelayClient()
        ev = EventFrame(event="interaction.resolved", payload={"done": True})
        c.set_response("interaction.resolve", (_ok({"ack": True}), [ev]))
        impl, _ = _impl(c)
        out = await impl.resolve_interaction(session_key="s", run_id="r1", response="ans")
        assert out["success"] is True
        assert out["followup_events"] == [("interaction.resolved", {"done": True})]
        _, args, kw = _last_call(c)
        assert kw["params"] == {"interactionId": "r1", "action": "submit", "message": "ans"}

    async def test_resolve_interaction_without_response(self):
        c = _FakeRelayClient()
        c.set_response("interaction.resolve", (_ok({}), []))
        impl, _ = _impl(c)
        await impl.resolve_interaction(session_key="s", run_id="r1")
        _, args, kw = _last_call(c)
        assert kw["params"] == {"interactionId": "r1", "action": "submit"}

    async def test_resolve_mode_transition(self):
        c = _FakeRelayClient()
        c.set_response("mode_transition.resolve", (_ok({}), []))
        impl, _ = _impl(c)
        out = await impl.resolve_mode_transition(session_key="s", run_id="r1", decision="proceed")
        assert out["success"] is True
        _, args, kw = _last_call(c)
        assert kw["params"] == {"transitionId": "r1", "decision": "proceed"}

    async def test_resolve_connect_exception(self):
        impl, _ = _impl(_FakeRelayClient())
        async def _bad_relay():
            raise ConnectionError("no relay")
        impl._relay = _bad_relay
        out = await impl.resolve_exec_approval(
            session_key="s", run_id="r1", decision="allow-once")
        assert out["success"] is False
        assert out["error"]["code"] == "INTERNAL_ERROR"

    async def test_resolve_rpc_exception(self):
        c = _FakeRelayClient()
        c.set_raise(ConnectionError("boom"))
        impl, _ = _impl(c)
        out = await impl.resolve_interaction(session_key="s", run_id="r1")
        assert out["success"] is False
        assert out["error"]["code"] == "INTERNAL_ERROR"


# ── _relay.py ────────────────────────────────────────────────────────────────


class TestRelayMixin:
    async def test_forward_request_no_request_id(self):
        c = _FakeRelayClient()
        c.set_response("foo.bar", _ok({"done": True}))
        impl, _ = _impl(c)
        out = await impl.relay_forward_request(method="foo.bar", params={"a": 1})
        assert out == {"success": True, "payload": {"done": True}}
        method, args, kw = _last_call(c)
        assert method == "send_request"
        assert args[0] == "foo.bar"
        assert kw["params"] == {"a": 1}

    async def test_forward_request_with_request_id(self):
        c = _FakeRelayClient()
        c.set_response("foo.bar", _ok({"done": True}))
        impl, _ = _impl(c)
        out = await impl.relay_forward_request(
            method="foo.bar", params={"a": 1}, request_id="req-1")
        assert out == {"success": True, "payload": {"done": True}}
        method, args, kw = _last_call(c)
        assert method == "send_request_with_id"
        assert kw["request_id"] == "req-1"

    async def test_forward_request_error(self):
        c = _FakeRelayClient()
        c.set_response("foo.bar", _err("BOOM", "nope"))
        impl, _ = _impl(c)
        out = await impl.relay_forward_request(method="foo.bar")
        assert out == {"success": False, "error": {"code": "BOOM", "message": "nope"}}

    async def test_forward_raw_frame_noop(self):
        impl, _ = _impl(_FakeRelayClient())
        out = await impl.relay_forward_raw_frame(frame={"type": "ping"})
        assert out == {"success": True}


# ── _models.py ───────────────────────────────────────────────────────────────


class TestModelsMixin:
    async def test_list_success_dict(self):
        c = _FakeRelayClient()
        c.set_response("models.list", _ok({"models": [{"id": "x"}, "bad", {"id": "y"}]}))
        impl, _ = _impl(c)
        assert await impl.models_list() == [{"id": "x"}, {"id": "y"}]
        _, args, kw = _last_call(c)
        assert kw["timeout"] == 5.0

    async def test_list_success_list_payload(self):
        c = _FakeRelayClient()
        c.set_response("models.list", _ok([{"id": "x"}]))
        impl, _ = _impl(c)
        assert await impl.models_list() == [{"id": "x"}]

    async def test_list_error_returns_empty(self):
        c = _FakeRelayClient()
        c.set_response("models.list", _err("X", "y"))
        impl, _ = _impl(c)
        assert await impl.models_list() == []

    async def test_list_empty_payload_returns_empty(self):
        c = _FakeRelayClient()
        c.set_response("models.list", _ok(None))
        impl, _ = _impl(c)
        assert await impl.models_list() == []

    async def test_list_non_list_entries(self):
        c = _FakeRelayClient()
        c.set_response("models.list", _ok({"models": "not-a-list"}))
        impl, _ = _impl(c)
        assert await impl.models_list() == []

    async def test_list_providers_success_dict(self):
        c = _FakeRelayClient()
        c.set_response("providers.list", _ok({"providers": [{"id": "anthropic"}]}))
        impl, _ = _impl(c)
        assert await impl.models_list_providers() == [{"id": "anthropic"}]

    async def test_list_providers_success_list(self):
        c = _FakeRelayClient()
        c.set_response("providers.list", _ok([{"id": "anthropic"}]))
        impl, _ = _impl(c)
        assert await impl.models_list_providers() == [{"id": "anthropic"}]

    async def test_list_providers_error_returns_empty(self):
        c = _FakeRelayClient()
        c.set_response("providers.list", _err("X", "y"))
        impl, _ = _impl(c)
        assert await impl.models_list_providers() == []


# ── _commands.py ─────────────────────────────────────────────────────────────


class TestCommandsMixin:
    async def test_list_no_scope(self):
        c = _FakeRelayClient()
        c.set_response("commands.list", _ok({"commands": [{"id": "c1"}, "bad", {"id": "c2"}]}))
        impl, _ = _impl(c)
        assert await impl.commands_list() == [{"id": "c1"}, {"id": "c2"}]
        _, args, kw = _last_call(c)
        assert args[0] == "commands.list" and kw["params"] == {}

    async def test_list_with_scope(self):
        c = _FakeRelayClient()
        c.set_response("commands.list", _ok({"commands": [{"id": "c1"}]}))
        impl, _ = _impl(c)
        await impl.commands_list(scope="builtin")
        _, args, kw = _last_call(c)
        assert kw["params"] == {"scope": "builtin"}

    async def test_list_list_payload(self):
        c = _FakeRelayClient()
        c.set_response("commands.list", _ok([{"id": "c1"}]))
        impl, _ = _impl(c)
        assert await impl.commands_list() == [{"id": "c1"}]

    async def test_list_error_returns_empty(self):
        c = _FakeRelayClient()
        c.set_response("commands.list", _err("X", "y"))
        impl, _ = _impl(c)
        assert await impl.commands_list() == []

    async def test_get_by_id_key(self):
        c = _FakeRelayClient()
        c.set_response("commands.get", _ok({"id": "c1", "name": "n"}))
        impl, _ = _impl(c)
        out = await impl.commands_get(command_id="c1")
        assert out == {"id": "c1", "name": "n"}
        _, args, kw = _last_call(c)
        assert kw["params"] == {"id": "c1"}

    async def test_get_by_name_key_for_slash_command(self):
        c = _FakeRelayClient()
        c.set_response("commands.get", _ok({"name": "/help"}))
        impl, _ = _impl(c)
        out = await impl.commands_get(command_id="/help")
        assert out == {"name": "/help"}
        _, args, kw = _last_call(c)
        assert kw["params"] == {"name": "/help"}

    async def test_get_error_returns_none(self):
        c = _FakeRelayClient()
        c.set_response("commands.get", _err("NOT_FOUND", "nope"))
        impl, _ = _impl(c)
        assert await impl.commands_get(command_id="c1") is None

    async def test_get_empty_payload_returns_none(self):
        c = _FakeRelayClient()
        c.set_response("commands.get", _ok(None))
        impl, _ = _impl(c)
        assert await impl.commands_get(command_id="c1") is None

    async def test_get_non_dict_payload_returns_none(self):
        c = _FakeRelayClient()
        c.set_response("commands.get", _ok(["x"]))
        impl, _ = _impl(c)
        assert await impl.commands_get(command_id="c1") is None
