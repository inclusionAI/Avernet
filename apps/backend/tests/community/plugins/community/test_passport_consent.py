"""ManualConsentPassportPlugin against the pinned mock-services facade contract.

The plugin under test is an HTTP client: every apply and query leg speaks to
the simulator's mock-services AgentPass service, so the test fixture is an
in-process :class:`ThreadingHTTPServer` implementing exactly the pinned wire
contract — title-authenticated facade routes, the success/failure envelope,
per- ``(tenant, bot_id, owner_workno)`` idempotency, 404s for unknown keys —
plus the decision endpoint the human surface would use, so tests can flip a
pending application the way the real mock would.

Pins: apply → PENDING + the mock's consent URLs and no token anywhere;
applyFirst → the trusted-bootstrap inline issue; the decision flip making
queryToken / queryAgentPassport read exactly what ``SelfIssuedPassportPlugin``
would have answered; the sticky rejection; the "still propagating" ``None``
for unknown keys; and fail-loud ``RuntimeError`` on transport failures and
error envelopes (no silent fallback).
"""
from __future__ import annotations

import json
import re
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agentclaw.community.plugins.community.passport import (
    SelfIssuedPassportPlugin,
    _token_for,
)
from agentclaw.community.plugins.community.passport_consent import (
    APPLICATION_STATUS_ISSUED,
    APPLICATION_STATUS_PENDING,
    APPLICATION_STATUS_REJECTED,
    ManualConsentPassportPlugin,
)
from agentclaw.community.utils.avernet_tenant import (
    avernet_tenant_scope,
    get_current_avernet_tenant,
)

_BASE_PATH = "/agentpass"
_DECISION_PATH_RE = re.compile(
    r"^" + re.escape(_BASE_PATH) + r"/applications/([^/]+)/decision$"
)
_HOST = "127.0.0.1"
_BOT = "sim-bot-1"
_OWNER = "workno-1"
_TENANT_B = "tenant-b"


class _AgentPassMock:
    """The mock-service's AgentPass registry, as seen from the stub server."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.applications: dict[str, dict] = {}
        self.by_key: dict[tuple[str, str, str], str] = {}
        self.apply_bodies: list[dict] = []
        self.query_params: list[dict] = []

    def record_apply(self, body: dict) -> None:
        with self.lock:
            self.apply_bodies.append(body)

    def record_query(self, query: dict) -> None:
        with self.lock:
            self.query_params.append(query)

    def register(
        self,
        *,
        tenant: str,
        bot_id: str,
        owner_workno: str,
        mcp_codes: list,
        window_seconds,
    ) -> dict:
        key = (tenant, bot_id, owner_workno)
        with self.lock:
            existing_id = self.by_key.get(key)
            if existing_id is not None:
                return self.applications[existing_id]
            application_id = f"sim-passport-{len(self.applications):04d}"
            application = {
                "application_id": application_id,
                "tenant": tenant,
                "bot_id": bot_id,
                "owner_workno": owner_workno,
                "status": APPLICATION_STATUS_PENDING,
                "mcps": list(mcp_codes),
                "window_seconds": window_seconds,
            }
            self.applications[application_id] = application
            self.by_key[key] = application_id
            return application

    def ensure_first_issued(
        self, *, tenant: str, bot_id: str, owner_workno: str
    ) -> dict:
        application = self.register(
            tenant=tenant,
            bot_id=bot_id,
            owner_workno=owner_workno,
            mcp_codes=[],
            window_seconds=None,
        )
        with self.lock:
            application["status"] = APPLICATION_STATUS_ISSUED
        return application

    def find(
        self, *, tenant: str, bot_id: str, owner_workno: str
    ) -> dict | None:
        key = (tenant, bot_id, owner_workno)
        with self.lock:
            application_id = self.by_key.get(key)
            if application_id is None:
                return None
            return self.applications[application_id]

    def decide(self, application_id: str, *, approve: bool) -> dict | None:
        with self.lock:
            application = self.applications.get(application_id)
            if application is None:
                return None
            if application["status"] != APPLICATION_STATUS_PENDING:
                return "already-decided"  # sentinel, distinct from an app dict
            application["status"] = (
                APPLICATION_STATUS_ISSUED if approve else APPLICATION_STATUS_REJECTED
            )
            return application


class _FacadeHandler(BaseHTTPRequestHandler):
    #: The per-fixture mock registry; set on the subclass by the fixture.
    state: _AgentPassMock
    #: bot_id that makes the mock answer a 500 failure envelope, for fail-loud tests.
    error_bot_id: str = "sim-bot-error"

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass

    # ── reply helpers ─────────────────────────────────────────────────────────

    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _failure(self, status: int, error_code: str) -> None:
        self._reply(status, {"success": False, "errorCode": error_code})

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    # ── auth (presence-only, as pinned) ───────────────────────────────────────

    def _post_auth_ok(self, body: dict) -> bool:
        return bool(body.get("appKey")) and bool(body.get("source"))

    def _get_auth_ok(self, query: dict) -> bool:
        return bool(query.get("source")) and (
            bool(query.get("code")) or bool(query.get("appKey"))
        )

    # ── POST routes ──────────────────────────────────────────────────────────

    def do_POST(self) -> None:  # noqa: N802 - stdlib signature
        path = urllib.parse.urlsplit(self.path).path
        if path == f"{_BASE_PATH}/facade/applyAgentPassport":
            self._handle_apply()
            return
        if path == f"{_BASE_PATH}/facade/applyFirstAgentPassport":
            self._handle_first()
            return
        match = _DECISION_PATH_RE.match(path)
        if match is not None:
            self._handle_decision(match.group(1))
            return
        self._failure(404, "NOT_FOUND")

    def _handle_apply(self) -> None:
        body = self._read_json()
        if not self._post_auth_ok(body):
            self._failure(401, "AUTH_MISSING")
            return
        self.state.record_apply(body)
        if body.get("bot_id") == self.error_bot_id:
            self._failure(500, "MOCK_APPLICATION_REFUSED")
            return
        application = self.state.register(
            tenant=body["tenant"],
            bot_id=body["bot_id"],
            owner_workno=body["owner_workno"],
            mcp_codes=body.get("mcp_codes") or [],
            window_seconds=body.get("window_seconds"),
        )
        self._reply(200, {"success": True, "data": self._apply_facts(application)})

    def _handle_first(self) -> None:
        body = self._read_json()
        if not self._post_auth_ok(body):
            self._failure(401, "AUTH_MISSING")
            return
        self.state.record_apply(body)
        if body.get("bot_id") == self.error_bot_id:
            self._failure(500, "MOCK_APPLICATION_REFUSED")
            return
        application = self.state.ensure_first_issued(
            tenant=body["tenant"],
            bot_id=body["bot_id"],
            owner_workno=body["owner_workno"],
        )
        self._reply(200, {"success": True, "data": self._apply_facts(application)})

    def _handle_decision(self, application_id: str) -> None:
        body = self._read_json()
        outcome = self.state.decide(application_id, approve=bool(body.get("approve")))
        if outcome is None:
            self._failure(404, "NOT_FOUND")
            return
        if outcome == "already-decided":
            self._failure(409, "ALREADY_DECIDED")
            return
        self._reply(
            200,
            {
                "success": True,
                "data": {
                    "application_id": application_id,
                    "status": outcome["status"],
                },
            },
        )

    # ── GET routes ───────────────────────────────────────────────────────────

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        query = dict(urllib.parse.parse_qsl(parsed.query))
        if not self._get_auth_ok(query):
            self._failure(401, "AUTH_MISSING")
            return
        recorded = {
            key: query[key]
            for key in ("bot_id", "owner_workno", "tenant", "source", "code")
            if key in query
        }
        self.state.record_query(recorded)
        application = self.state.find(
            tenant=query.get("tenant", ""),
            bot_id=query.get("bot_id", ""),
            owner_workno=query.get("owner_workno", ""),
        )
        if application is None:
            self._failure(404, "NOT_FOUND")
            return
        if path == f"{_BASE_PATH}/facade/queryAuthStatus":
            self._reply(
                200,
                {
                    "success": True,
                    "data": {
                        "status": application["status"],
                        "application_id": application["application_id"],
                    },
                },
            )
            return
        if path == f"{_BASE_PATH}/facade/queryToken":
            if application["status"] == APPLICATION_STATUS_ISSUED:
                auth_val = _token_for(application["bot_id"])
                self._reply(
                    200,
                    {"success": True, "data": {"token": auth_val}},
                )
                return
            self._failure(404, "NOT_FOUND")
            return
        if path == f"{_BASE_PATH}/facade/queryAgentPassport":
            if application["status"] == APPLICATION_STATUS_ISSUED:
                self._reply(
                    200,
                    {
                        "success": True,
                        "data": {
                            "agent_id": application["bot_id"],
                            "agent_code": application["bot_id"],
                            "status": APPLICATION_STATUS_ISSUED,
                            "access_mode": None,
                            "mcps": [],
                            "clis": [],
                        },
                    },
                )
                return
            self._failure(404, "NOT_FOUND")
            return
        self._failure(404, "NOT_FOUND")

    # ── shaping ──────────────────────────────────────────────────────────────

    def _apply_facts(self, application: dict) -> dict:
        status = application["status"]
        issued = status == APPLICATION_STATUS_ISSUED
        if issued:
            iframe_url = redirect_url = None
        else:
            origin = f"http://{self.headers.get('Host')}{_BASE_PATH}"
            iframe_url = (
                f"{origin}/applications/{application['application_id']}/consent"
                if status == APPLICATION_STATUS_PENDING
                else None
            )
            redirect_url = (
                f"{origin}/applications/{application['application_id']}"
                if status == APPLICATION_STATUS_PENDING
                else None
            )
        auth_val = _token_for(application["bot_id"]) if issued else None
        return {
            "application_id": application["application_id"],
            "status": status,
            "token": auth_val,
            "agent_code": application["bot_id"] if issued else None,
            "iframe_url": iframe_url,
            "redirect_url": redirect_url,
            "deadline": "2030-01-01T00:00:00+08:00",
        }


@pytest.fixture()
def mock_services():
    state = _AgentPassMock()

    class _Handler(_FacadeHandler):
        pass

    _Handler.state = state
    server = ThreadingHTTPServer((_HOST, 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://{server.server_address[0]}:{server.server_address[1]}{_BASE_PATH}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture()
def plugin(mock_services) -> ManualConsentPassportPlugin:
    _, base_url = mock_services
    return ManualConsentPassportPlugin(consent_base_url=base_url)


def _apply(plugin: ManualConsentPassportPlugin, **overrides):
    kwargs = dict(
        bot_id=_BOT,
        owner_workno=_OWNER,
        mcp_codes=["mcp-a"],
        bot_name="Simulator Bot",
        bot_desc="a manual-consent fixture bot",
        engine_type="openclaw",
    )
    kwargs.update(overrides)
    return plugin.apply_agent_passport(**kwargs)


def _sole_application(state: _AgentPassMock) -> dict:
    with state.lock:
        assert len(state.applications) == 1
        return next(iter(state.applications.values()))


def _decide(mock_services, application_id: str, *, approve: bool) -> dict:
    _, base_url = mock_services
    request = urllib.request.Request(
        f"{base_url}/applications/{application_id}/decision",
        data=json.dumps({"approve": approve}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


# ── the apply side ────────────────────────────────────────────────────────────


def test_apply_returns_the_pending_application_and_its_consent_urls(plugin, mock_services):
    state, _ = mock_services
    result = _apply(plugin)

    assert result["token"] is None
    assert result["agent_code"] is None

    application = _sole_application(state)
    assert application["status"] == APPLICATION_STATUS_PENDING
    assert result["iframe_url"] == (
        f"{plugin._base_url}/applications/{application['application_id']}/consent"
    )
    assert result["redirect_url"] == (
        f"{plugin._base_url}/applications/{application['application_id']}"
    )


def test_reapply_is_idempotent_per_key(plugin, mock_services):
    state, _ = mock_services
    first = _apply(plugin)
    second = _apply(plugin, bot_name="Another name")

    assert second["iframe_url"] == first["iframe_url"]
    assert second["redirect_url"] == first["redirect_url"]
    assert _sole_application(state)["status"] == APPLICATION_STATUS_PENDING


def test_apply_sends_the_pinned_request_fields(plugin, mock_services):
    state, _ = mock_services
    _apply(plugin)

    body = state.apply_bodies[0]
    assert body["bot_id"] == _BOT
    assert body["owner_workno"] == _OWNER
    assert body["tenant"] == get_current_avernet_tenant()
    assert body["mcp_codes"] == ["mcp-a"]
    assert body["window_seconds"] == 180
    assert body["appKey"]
    assert body["source"]


def test_error_envelope_on_apply_fails_loud(plugin):
    with pytest.raises(RuntimeError):
        _apply(plugin, bot_id="sim-bot-error")


def test_first_passport_issues_inline(plugin):
    result = plugin.apply_first_agent_passport(
        bot_id=_BOT, owner_workno=_OWNER, mcp_codes=[]
    )

    auth_val = _token_for(_BOT)
    assert result == {
        "token": auth_val,
        "agent_code": _BOT,
        "iframe_url": None,
        "redirect_url": None,
    }
    assert plugin.query_auth_status(_BOT, _OWNER) == {
        "status": APPLICATION_STATUS_ISSUED,
        "application_id": "sim-passport-0000",
    }
    assert plugin.query_token(_BOT, _OWNER) == _token_for(_BOT)


def test_first_passport_then_apply_serves_the_issued_answer(plugin):
    plugin.apply_first_agent_passport(bot_id=_BOT, owner_workno=_OWNER, mcp_codes=[])
    result = _apply(plugin)

    auth_val = _token_for(_BOT)
    assert result == {
        "token": auth_val,
        "agent_code": _BOT,
        "iframe_url": None,
        "redirect_url": None,
    }


# ── the read side ─────────────────────────────────────────────────────────────


def test_pending_reads_answer_the_still_propagating_vocabulary(plugin, mock_services):
    state, _ = mock_services
    _apply(plugin)
    application = _sole_application(state)

    assert plugin.query_auth_status(_BOT, _OWNER) == {
        "status": APPLICATION_STATUS_PENDING,
        "application_id": application["application_id"],
    }
    assert plugin.query_token(_BOT, _OWNER) is None
    assert plugin.query_agent_passport(_BOT, _OWNER) is None

    recorded = state.query_params[0]
    assert recorded["source"] == "sim"
    assert recorded["code"] == "agentpass-sim"
    assert recorded["tenant"] == get_current_avernet_tenant()


def test_unknown_key_queries_answer_none(plugin):
    assert plugin.query_auth_status("other-bot", _OWNER) is None
    assert plugin.query_token("other-bot", _OWNER) is None
    assert plugin.query_agent_passport("other-bot", _OWNER) is None


def test_query_target_env_is_validated(plugin):
    with pytest.raises(ValueError):
        plugin.query_token(_BOT, _OWNER, target_env="staging")
    with pytest.raises(ValueError):
        plugin.apply_first_agent_passport(
            bot_id=_BOT, owner_workno=_OWNER, mcp_codes=[], target_env="staging"
        )


def test_approval_flips_to_issued_with_self_issued_parity(plugin, mock_services):
    state, _ = mock_services
    _apply(plugin)
    application = _sole_application(state)

    outcome = _decide(mock_services, application["application_id"], approve=True)
    assert outcome["data"]["status"] == APPLICATION_STATUS_ISSUED

    self_issued = SelfIssuedPassportPlugin()
    assert plugin.query_auth_status(_BOT, _OWNER) == {
        "status": APPLICATION_STATUS_ISSUED,
        "application_id": application["application_id"],
    }
    assert plugin.query_token(_BOT, _OWNER) == self_issued.query_token(_BOT, _OWNER)
    assert plugin.query_agent_passport(_BOT, _OWNER) == self_issued.query_agent_passport(
        _BOT, _OWNER
    )
    assert plugin.query_token(_BOT, _OWNER) == f"community-passport-{_BOT}"
    assert plugin.query_passport_clis(_BOT, _OWNER) == []


def test_rejection_flips_to_rejected_and_stays_sticky(plugin, mock_services):
    state, _ = mock_services
    _apply(plugin)
    application = _sole_application(state)

    outcome = _decide(
        mock_services, application["application_id"], approve=False
    )
    assert outcome["data"]["status"] == APPLICATION_STATUS_REJECTED

    assert plugin.query_auth_status(_BOT, _OWNER) == {
        "status": APPLICATION_STATUS_REJECTED,
        "application_id": application["application_id"],
    }
    assert plugin.query_token(_BOT, _OWNER) is None
    assert plugin.query_agent_passport(_BOT, _OWNER) is None

    assert _apply(plugin) == {
        "token": None,
        "agent_code": None,
        "iframe_url": None,
        "redirect_url": None,
    }


def test_deciding_twice_is_refused_by_the_mock(plugin, mock_services):
    state, _ = mock_services
    _apply(plugin)
    application = _sole_application(state)
    _decide(mock_services, application["application_id"], approve=True)

    with pytest.raises(urllib.error.HTTPError) as error:
        _decide(mock_services, application["application_id"], approve=False)
    assert error.value.code == 409


# ── tenancy & fail-loud ───────────────────────────────────────────────────────


def test_tenant_travels_with_every_facade_call(plugin, mock_services):
    state, _ = mock_services
    _apply(plugin)

    with avernet_tenant_scope(_TENANT_B):
        _apply(plugin)
        # Key-scoped reads stay isolated: an owner who never applied under
        # tenant-b is unknown there even though (bot_id, owner) exists under
        # the default tenant.
        assert plugin.query_auth_status(_BOT, "owner-never-applied-here") is None

    with state.lock:
        keys = set(state.by_key)
        default_tenant_id = state.by_key[
            (get_current_avernet_tenant(), _BOT, _OWNER)
        ]
    assert keys == {
        (get_current_avernet_tenant(), _BOT, _OWNER),
        (_TENANT_B, _BOT, _OWNER),
    }
    assert plugin.query_auth_status(_BOT, _OWNER) == {
        "status": APPLICATION_STATUS_PENDING,
        "application_id": default_tenant_id,
    }


def test_transport_failure_fails_loud_everywhere():
    with socket.socket() as probe:
        probe.bind((_HOST, 0))
        closed_port = probe.getsockname()[1]
    offline = ManualConsentPassportPlugin(
        consent_base_url=f"http://{_HOST}:{closed_port}{_BASE_PATH}"
    )

    with pytest.raises(RuntimeError):
        offline.apply_agent_passport(
            bot_id=_BOT, owner_workno=_OWNER, mcp_codes=[]
        )
    with pytest.raises(RuntimeError):
        offline.query_auth_status(_BOT, _OWNER)
    with pytest.raises(RuntimeError):
        offline.query_token(_BOT, _OWNER)
    with pytest.raises(RuntimeError):
        offline.apply_first_agent_passport(
            bot_id=_BOT, owner_workno=_OWNER, mcp_codes=[]
        )


# ── the delegated stateless legs ──────────────────────────────────────────────


def test_the_remaining_legs_delegate_to_the_self_issued_base(plugin):
    plugin.update_passport(_BOT, _OWNER, bot_name="Rename")
    plugin.destroy_passport(_BOT, _OWNER)
    plugin.freeze_agent_passport(_BOT, _OWNER, reason="test")
    plugin.unfreeze_agent_passport(_BOT, _OWNER, reason="test")
    assert plugin.save_sub_resources(_BOT, _OWNER, []) is True