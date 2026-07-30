"""Endpoint tests for the sessions group (Track C, Task 7)."""

from __future__ import annotations

import pytest

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions import router
from agentclaw.community.core.engine_runtime.errors import (
    EngineCapabilityUnsupportedError,
    EngineDeviceNotReadyError,
    EngineResourceNotFoundError,
    EngineUpstreamError,
)
from agentclaw.community.core.engine_runtime.models import EngineResult
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterTimeoutError,
)

from .conftest import BOT, OWNER, fails, ok

SESSION_ID = "session:2d20edc1-2f84-4524-8486-15bbd7078d42:user:165137"

ENGINE_SESSION = {
    "id": SESSION_ID,
    "title": "Quarterly report",
    "user_id": OWNER,
    "agent_id": "main",
    "model": "openai/gpt-5.3",
    "permission_mode": "on-miss",
    "cwd": "/workspace",
    "gmt_created": "2026-07-30T09:00:00+00:00",
    "gmt_modified": "2026-07-30T09:12:04+00:00",
    "message_count": 2,
    "ext_info": {"internal": "should not be published"},
}

ENGINE_MESSAGE = {
    "id": "m1",
    "session_id": SESSION_ID,
    "role": "assistant",
    "content": "Done.",
    "metadata": {"internal_trace": "sandbox-7f3a"},
    "gmt_created": "2026-07-30T09:12:04+00:00",
}


@pytest.fixture
def client(make_client):
    return make_client(router)


def _base(bot: str = BOT) -> str:
    return f"/openapi/v1/bots/{bot}/sessions"


# ── success paths ─────────────────────────────────────────────────────────────


def test_list_sessions(client, relay):
    relay.results = [EngineResult(data=[ENGINE_SESSION])]
    data = ok(client.get(_base()))
    assert data["total"] == 1
    assert data["items"][0]["session_id"] == SESSION_ID
    assert relay.paths == ["/api/sessions"]


@pytest.mark.parametrize("filter_name", ["agent_id", "session_key"])
def test_list_filters_are_forwarded_to_the_engine(client, relay, filter_name):
    """Both filters are applied upstream *before* pagination, so they must
    travel with the window — filtering the returned page instead would hand
    back short pages that do not line up with the requested one."""
    relay.results = [EngineResult(data=[ENGINE_SESSION])]
    ok(client.get(_base(), params={filter_name: "v"}))
    assert relay.calls[0]["params"][filter_name] == "v"


def test_list_omits_filters_the_caller_did_not_send(client, relay):
    relay.results = [EngineResult(data=[ENGINE_SESSION])]
    ok(client.get(_base()))
    assert set(relay.calls[0]["params"]) == {"offset", "limit"}


def test_list_does_not_publish_engine_only_fields(client, relay):
    """``user_id`` is the caller and ``ext_info`` is an opaque engine bag."""
    relay.results = [EngineResult(data=[ENGINE_SESSION])]
    item = ok(client.get(_base()))["items"][0]
    assert "user_id" not in item
    assert "ext_info" not in item
    assert "should not be published" not in str(item)


def test_create_session_fills_user_id_from_the_principal(client, relay):
    relay.results = [EngineResult(data=ENGINE_SESSION)]
    resp = client.post(_base(), json={"title": "T"})
    assert resp.status_code == 201, resp.json()
    assert relay.calls[0]["body"]["user_id"] == OWNER


def test_get_session_uses_the_id_verbatim(client, relay):
    """Colons are legal in a path segment; no encoding scheme is applied."""
    relay.results = [EngineResult(data=ENGINE_SESSION)]
    ok(client.get(f"{_base()}/{SESSION_ID}"))
    assert relay.paths == [f"/api/sessions/{SESSION_ID}"]


def test_patch_sends_query_params_not_a_body(client, relay):
    """The engine's update route declares bare scalars, which FastAPI binds
    from the **query string** — there is no Body(...) on it.

    Sending a body is silently discarded: the engine builds its update request
    with every field None and answers 200 with the unchanged session, a no-op
    that looks like success. Asserting only that *a* body was passed could not
    tell the two apart, which is how this shipped once already.
    """
    relay.results = [EngineResult(data=ENGINE_SESSION)]
    ok(client.patch(f"{_base()}/{SESSION_ID}", json={"title": "New"}))
    assert relay.calls[0]["method"] == "POST"
    assert relay.calls[0]["path"] == f"/api/sessions/{SESSION_ID}/update"
    assert relay.calls[0]["params"] == {"title": "New"}
    assert relay.calls[0]["body"] is None


def test_patch_omits_unset_fields(client, relay):
    """A partial update must not blank fields the caller did not send."""
    relay.results = [EngineResult(data=ENGINE_SESSION)]
    ok(client.patch(f"{_base()}/{SESSION_ID}", json={"model": "m"}))
    assert relay.calls[0]["params"] == {"model": "m"}


def test_patch_rejects_a_field_an_engine_would_silently_drop(client, relay):
    """``cwd`` was offered and withdrawn: one bundled engine applies it and the
    other discards it without saying so, which would make the same request
    succeed and do nothing depending on the bot's engine. 422 beats a no-op
    that reports 200."""
    resp = client.patch(f"{_base()}/{SESSION_ID}", json={"cwd": "/work"})
    assert resp.status_code == 422, resp.json()
    assert relay.calls == []


def test_message_total_prefers_the_engines_count(client, relay):
    """Unlike the session list, the history route does report a total."""
    relay.results = [EngineResult(data=[ENGINE_MESSAGE], total=1200)]
    assert ok(client.get(f"{_base()}/{SESSION_ID}/messages"))["total"] == 1200


def test_delete_session(client, relay):
    assert ok(client.delete(f"{_base()}/{SESSION_ID}"))["deleted"] is True
    assert relay.calls[0]["method"] == "DELETE"


def test_list_messages_drops_engine_metadata(client, relay):
    relay.results = [EngineResult(data=[ENGINE_MESSAGE])]
    data = ok(client.get(f"{_base()}/{SESSION_ID}/messages"))
    assert data["items"][0]["role"] == "assistant"
    assert "metadata" not in data["items"][0]
    assert "sandbox-7f3a" not in str(data)


def test_unknown_message_role_does_not_500(client, relay):
    """A stub or newer engine returning an unlisted role must not break a read."""
    relay.results = [EngineResult(data=[{**ENGINE_MESSAGE, "role": "wat"}])]
    assert ok(client.get(f"{_base()}/{SESSION_ID}/messages"))["items"][0][
        "role"
    ] == "system"


def test_clear_messages(client, relay):
    assert ok(client.delete(f"{_base()}/{SESSION_ID}/messages"))["deleted"] is True


def _sessions(n: int) -> list[dict]:
    return [{**ENGINE_SESSION, "id": f"s{i}"} for i in range(n)]


def test_a_late_page_is_fetched_from_the_engine_not_sliced_locally(client, relay):
    """The engine query follows the caller's page.

    Fetching a fixed prefix from offset 0 and slicing made every page past that
    prefix come back empty; the window has to move with the caller.
    """
    relay.results = [EngineResult(data=_sessions(3))]
    data = ok(client.get(_base(), params={"page": 400, "page_size": 3}))
    assert relay.calls[0]["params"] == {"offset": 1197, "limit": 4}
    assert [i["session_id"] for i in data["items"]] == ["s0", "s1", "s2"]


def test_pagination_total_is_exact_once_the_caller_reaches_the_end(client, relay):
    """A window shorter than the page proves nothing follows it."""
    relay.results = [EngineResult(data=_sessions(2))]
    data = ok(client.get(_base(), params={"page": 2, "page_size": 3}))
    assert data["total"] == 5
    assert len(data["items"]) == 2


def test_pagination_total_is_a_floor_while_more_remain(client, relay):
    """The lookahead item proves more exist without inventing a count."""
    relay.results = [EngineResult(data=_sessions(4))]
    data = ok(client.get(_base(), params={"page": 2, "page_size": 3}))
    assert data["total"] == 7
    assert [i["session_id"] for i in data["items"]] == ["s0", "s1", "s2"]


def test_message_window_asks_for_enough_to_reach_the_offset(client, relay):
    """The history route bounds its *fetch* by ``limit`` and only then skips
    ``offset``, so a page-sized limit slices out of a prefix too short to
    contain the page — every page but the first came back empty."""
    relay.results = [EngineResult(data=[ENGINE_MESSAGE], total=1200)]
    ok(client.get(f"{_base()}/{SESSION_ID}/messages", params={"page": 3, "page_size": 50}))
    assert relay.calls[0]["params"] == {"offset": 100, "limit": 151}


def test_the_session_window_stays_page_sized(client, relay):
    """The session list paginates a materialised list, so ``limit`` there really
    is a page size and must not grow with the offset."""
    relay.results = [EngineResult(data=[ENGINE_SESSION])]
    ok(client.get(_base(), params={"page": 3, "page_size": 50}))
    assert relay.calls[0]["params"] == {"offset": 100, "limit": 51}


# ── the personal-bots-only gate ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "suffix"),
    [
        ("get", ""), ("post", ""), ("get", f"/{SESSION_ID}"),
        ("patch", f"/{SESSION_ID}"), ("delete", f"/{SESSION_ID}"),
        ("get", f"/{SESSION_ID}/messages"), ("delete", f"/{SESSION_ID}/messages"),
    ],
)
def test_service_bot_gets_501_without_touching_the_device(
    client, relay, method, suffix
):
    """All seven routes, gated BEFORE the forward.

    A filter applied to what the device returned would already have fetched
    every caller's sessions — so the assertion is that no call happened, not
    merely that the status was 501.
    """
    relay.set_bot_type("service")
    kwargs = {"json": {}} if method in ("post", "patch") else {}
    resp = getattr(client, method)(f"{_base()}{suffix}", **kwargs)
    body = fails(resp, 501)
    assert body["message"] == "Not supported for this bot type"
    assert relay.calls == []


# ── isolation ────────────────────────────────────────────────────────────────


def test_foreign_bot_is_a_masked_404_with_no_device_call(client, relay):
    resp = client.get(_base("someone-elses-bot"))
    assert fails(resp, 404)["message"] == "Not found"
    assert relay.calls == []


def test_caller_supplied_identity_is_rejected(client, relay):
    """``user_id``/``engine`` must come from the server, never the request."""
    for payload in ({"user_id": "someone-else"}, {"engine": "aicoding"}):
        resp = client.post(_base(), json={"title": "T", **payload})
        assert resp.status_code == 422, resp.json()
    assert relay.calls == []


# ── mapped errors ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("exc", "status", "message"),
    [
        (EngineDeviceNotReadyError("cold"), 409, "Bot device is not ready"),
        (EngineResourceNotFoundError("gone"), 404, "Not found"),
        (EngineUpstreamError("boom"), 502, "Engine service error"),
        (DeviceAdapterTimeoutError("slow"), 504, "Engine request timed out"),
        (
            EngineCapabilityUnsupportedError("nope"),
            501,
            "Not supported by this bot's engine; see the engine capabilities endpoint",
        ),
    ],
)
def test_relay_errors_are_enveloped(client, relay, exc, status, message):
    relay.raises = exc
    assert fails(client.get(_base()), status)["message"] == message


def test_missing_session_payload_is_404_not_500(client, relay):
    relay.results = [EngineResult(data=None)]
    assert fails(client.get(f"{_base()}/{SESSION_ID}"), 404)["message"] == "Not found"
