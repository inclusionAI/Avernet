"""Endpoint tests for the approvals group (Track C, Task 9)."""

from __future__ import annotations

import pytest

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.approvals import router
from agentclaw.community.core.engine_runtime.models import EngineResult

from .conftest import BOT, OWNER, fails, ok

SESSION = "session:2d20edc1:user:165137"
BASE = f"/openapi/v1/bots/approvals/{BOT}"


@pytest.fixture
def client(make_client):
    return make_client(router)


def test_get_mode_is_a_query_not_a_post_body(client, relay):
    relay.results = [EngineResult(data={"mode": "on-miss", "sessionKey": SESSION})]
    data = ok(client.get(f"{BASE}/mode", params={"session_key": SESSION}))
    assert data == {"session_key": SESSION, "mode": "on-miss"}
    assert relay.calls[0]["path"] == "/api/approvals/mode/get"
    assert relay.calls[0]["body"]["user_id"] == OWNER


def test_get_mode_requires_a_session_key(client, relay):
    assert client.get(f"{BASE}/mode").status_code == 422
    assert relay.calls == []


def test_set_mode_forwards_the_value_verbatim(client, relay):
    relay.results = [EngineResult(data={"mode": "never", "sessionKey": SESSION})]
    ok(client.put(f"{BASE}/mode", json={"session_key": SESSION, "mode": "never"}))
    assert relay.calls[0]["body"] == {
        "session_key": SESSION,
        "mode": "never",
        "user_id": OWNER,
    }


def test_a_refused_set_is_not_reported_as_applied(client, relay):
    """``exec.approvals.set`` reports the call and the change separately: the
    envelope succeeds while ``data.ok`` says the mode was not applied. Echoing
    the requested mode back would claim a change that never happened."""
    relay.results = [
        EngineResult(data={"ok": False, "mode": "never", "sessionKey": SESSION})
    ]
    resp = client.put(f"{BASE}/mode", json={"session_key": SESSION, "mode": "never"})
    assert resp.status_code == 502


def test_an_applied_set_is_reported_as_applied(client, relay):
    relay.results = [
        EngineResult(data={"ok": True, "mode": "never", "sessionKey": SESSION})
    ]
    data = ok(client.put(f"{BASE}/mode", json={"session_key": SESSION, "mode": "never"}))
    assert data["mode"] == "never"


def test_a_read_without_an_ok_flag_is_not_treated_as_refused(client, relay):
    """The read route's payload carries no ``ok`` — a missing flag is not a
    refusal, which is why the check is ``is False`` rather than falsy."""
    relay.results = [EngineResult(data={"mode": "on-miss", "sessionKey": SESSION})]
    data = ok(client.get(f"{BASE}/mode", params={"session_key": SESSION}))
    assert data["mode"] == "on-miss"


@pytest.mark.parametrize("mode", ["approve", "on-miss", "never"])
def test_advertised_modes_are_accepted(client, relay, mode):
    relay.results = [EngineResult(data={"mode": mode, "sessionKey": SESSION})]
    ok(client.put(f"{BASE}/mode", json={"session_key": SESSION, "mode": mode}))


@pytest.mark.parametrize("alias", ["always", "on_miss", "off", "auto"])
def test_undocumented_aliases_are_rejected(client, relay, alias):
    """The engine accepts six spellings; publishing them would bless two
    public names for one mode, permanently."""
    resp = client.put(f"{BASE}/mode", json={"session_key": SESSION, "mode": alias})
    assert resp.status_code == 422, resp.json()
    assert relay.calls == []


def test_a_stub_returning_auto_does_not_500(client, relay):
    """The response mode is a plain string precisely so this cannot break."""
    relay.results = [EngineResult(data={"mode": "auto", "sessionKey": SESSION})]
    assert ok(client.get(f"{BASE}/mode", params={"session_key": SESSION}))[
        "mode"
    ] == "auto"


def test_caller_supplied_user_id_is_rejected(client, relay):
    resp = client.put(
        f"{BASE}/mode",
        json={"session_key": SESSION, "mode": "never", "user_id": "someone-else"},
    )
    assert resp.status_code == 422
    assert relay.calls == []


# ── /modes: gated, unlike the engine's own route ─────────────────────────────


def _caps(*supported: str) -> EngineResult:
    return EngineResult(data={"supported": list(supported)})


def test_modes_lists_the_three_advertised_values_in_english(client, relay):
    relay.results = [_caps("approval.get", "approval.set")]
    data = ok(client.get(f"{BASE}/modes"))
    assert [m["value"] for m in data] == ["approve", "on-miss", "never"]
    assert all(m["description"] and m["description"].isascii() for m in data)


def test_modes_is_gated_where_the_engines_route_is_not(client, relay):
    """On an engine declaring no approval capability, the engine's own route
    still advertises three modes while get/set both 501. All three public
    routes agree instead."""
    relay.results = [_caps("session.list")]
    body = fails(client.get(f"{BASE}/modes"), 501)
    assert "capabilities" in body["message"]


def test_modes_accepts_a_limited_capability(client, relay):
    relay.results = [EngineResult(data={"supported": [], "limited": ["approval.set"]})]
    assert len(ok(client.get(f"{BASE}/modes"))) == 3


def test_modes_501s_on_a_read_only_engine(client, relay):
    """approval.get and approval.set are independent engine capabilities, and
    the engine gates its own two routes on one each. On an engine declaring the
    read but not the write, nothing is selectable — listing the three modes
    would advertise a write that answers 501 for every one of them."""
    relay.results = [_caps("approval.get")]
    assert fails(client.get(f"{BASE}/modes"), 501)


def test_modes_serves_a_write_only_engine(client, relay):
    """The mirror case: the write is what this route describes, so declaring it
    without the read still leaves three genuinely settable values."""
    relay.results = [_caps("approval.set")]
    assert len(ok(client.get(f"{BASE}/modes"))) == 3


def test_foreign_bot_is_masked_404_without_a_device_call(client, relay):
    assert fails(client.get("/openapi/v1/bots/approvals/other/modes"), 404)
    assert relay.calls == []
