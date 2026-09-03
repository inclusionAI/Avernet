"""Endpoint tests for the sessions group (Track C, Task 7)."""

from __future__ import annotations

import pytest

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions import router
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions import (
    converter_creation,
)
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


def test_list_sessions_hides_openclaw_internal_title_suffix(client, relay):
    canonical_id = f"agent:main:{SESSION_ID}"
    relay.results = [EngineResult(data=[{
        **ENGINE_SESSION,
        "id": canonical_id,
        "title": "Quarterly_report_2d20edc1-2f84-4524-8486-15bbd7078d42",
    }])]

    item = ok(client.get(_base()))["items"][0]

    assert item["title"] == "Quarterly_report"


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


def test_list_favorites_forwards_the_verified_user_and_page(client, relay):
    relay.results = [EngineResult(data=[ENGINE_SESSION])]
    data = ok(
        client.get(
            f"{_base()}/favorites",
            params={"page": 2, "page_size": 3, "agent_id": "main"},
        )
    )
    assert data["items"][0]["session_id"] == SESSION_ID
    assert relay.calls[0]["path"] == "/api/session-favorites"
    assert relay.calls[0]["params"] == {
        "offset": 3,
        "limit": 4,
        "user_id": OWNER,
        "agent_id": "main",
    }


def test_list_favorites_does_not_publish_engine_only_fields(client, relay):
    relay.results = [EngineResult(data=[ENGINE_SESSION])]
    item = ok(client.get(f"{_base()}/favorites"))["items"][0]
    assert "user_id" not in item
    assert "ext_info" not in item


@pytest.mark.parametrize(
    ("method", "favorited", "upstream_method"),
    [("put", True, "PUT"), ("delete", False, "DELETE")],
)
def test_set_favorite_is_idempotent_and_encodes_only_the_upstream_path(
    client, relay, method, favorited, upstream_method
):
    data = ok(getattr(client, method)(f"{_base()}/{SESSION_ID}/favorite"))
    assert data == {"session_id": SESSION_ID, "favorited": favorited}
    assert relay.calls[0]["method"] == upstream_method
    assert relay.calls[0]["path"] == (
        "/api/session-favorites/"
        "session%3A2d20edc1-2f84-4524-8486-15bbd7078d42%3Auser%3A165137"
    )
    assert relay.calls[0]["params"] == {"user_id": OWNER}


def test_create_session_fills_user_id_from_the_principal(client, relay):
    canonical = {
        **ENGINE_SESSION,
        "id": f"agent:main:{SESSION_ID}",
        "title": "T_2d20edc1-2f84-4524-8486-15bbd7078d42",
        "model": "openai/gpt-5.4",
        "gmt_created": "2026-07-30T09:00:01+00:00",
        "gmt_modified": "2026-07-30T09:00:01+00:00",
    }
    relay.results = [
        EngineResult(data={**ENGINE_SESSION, "title": "T_suffix"}),
        EngineResult(data=[canonical]),
    ]
    resp = client.post(_base(), json={"title": "T"})
    assert resp.status_code == 201, resp.json()
    assert relay.calls[0]["body"]["user_id"] == OWNER
    data = resp.json()["data"]
    assert data["session_id"] == canonical["id"]
    assert data["model"] == "openai/gpt-5.4"
    assert data["title"] == "T"
    assert data["gmt_create"] == ENGINE_SESSION["gmt_created"]
    assert data["gmt_modified"] == ENGINE_SESSION["gmt_modified"]
    assert relay.calls[1]["method"] == "GET"
    assert relay.calls[1]["path"] == "/api/sessions"


def test_create_session_other_engine_does_not_add_reconciliation_call(client, relay):
    relay.bots[(BOT, OWNER)] = relay.bots[(BOT, OWNER)].__class__(
        bot_id=BOT, bot_type="personal", active_engine="claude_code", owner_id=OWNER,
    )
    relay.results = [EngineResult(data=ENGINE_SESSION)]

    resp = client.post(_base(), json={"title": "T"})

    assert resp.status_code == 201, resp.json()
    assert relay.paths == ["/api/sessions"]


def test_create_session_keeps_success_when_openclaw_reconciliation_fails(
    client, relay, monkeypatch
):
    monkeypatch.setattr(converter_creation, "_DELAYS", (0.0, 0.0, 0.0))
    relay.results = [EngineResult(data=ENGINE_SESSION)]
    original_call = relay.call
    list_attempts = 0

    async def fail_only_list(**kwargs):
        nonlocal list_attempts
        if kwargs["method"] == "GET":
            list_attempts += 1
            raise EngineUpstreamError("list temporarily unavailable")
        return await original_call(**kwargs)

    relay.call = fail_only_list

    resp = client.post(_base(), json={"title": "T"})

    assert resp.status_code == 201, resp.json()
    assert resp.json()["data"]["session_id"] == SESSION_ID
    assert list_attempts == 3


def test_create_rejects_an_agent_one_engine_would_silently_drop(client, relay):
    """``agent_id`` was offered on create and withdrawn, for the reason ``cwd``
    was withdrawn from PATCH.

    ``claude_code`` encodes the agent into the session key, so later reads
    recover it; ``openclaw`` builds ``session:{uuid}:user:{user_id}`` without it
    and then synthesises a 201 echoing the value it dropped. The same request
    would attach or not depending on the bot's engine, and the response would
    claim it attached either way — a 422 beats a 201 that misreports.
    """
    resp = client.post(_base(), json={"title": "T", "agent_id": "main"})
    assert resp.status_code == 422, resp.json()
    assert relay.calls == []


def test_create_does_not_forward_an_agent_field(client, relay):
    """The field is gone from the model, so nothing may reach the engine under
    that key — an empty value forwarded anyway would still read as 'no agent'
    on an engine that treats absence and blank differently."""
    relay.results = [EngineResult(data=ENGINE_SESSION)]
    resp = client.post(_base(), json={"title": "T"})
    assert resp.status_code == 201, resp.json()
    assert "agent_id" not in relay.calls[0]["body"]


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
    assert (
        ok(client.get(f"{_base()}/{SESSION_ID}/messages"))["items"][0]["role"]
        == "system"
    )


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


def _messages(n: int) -> list[dict]:
    """``n`` messages oldest-first, the order the engine returns them in."""
    return [{**ENGINE_MESSAGE, "id": f"m{i}"} for i in range(n)]


def test_the_history_window_sends_no_offset_and_a_covering_limit(client, relay):
    """The history route tail-limits instead of paginating, so the offset is
    applied locally; sending it would cancel against the tail."""
    relay.results = [EngineResult(data=_messages(1))]
    ok(
        client.get(
            f"{_base()}/{SESSION_ID}/messages", params={"page": 3, "page_size": 50}
        )
    )
    assert relay.calls[0]["params"] == {"offset": 0, "limit": 151}


def test_history_pages_run_newest_first_without_repeating(client, relay):
    """The regression this replaces: growing ``limit`` with the offset moved the
    tail's start back by exactly the offset, so page 2 re-served page 1."""
    # 100 messages, page_size 20. Page 1 asks for the newest 21, page 2 the
    # newest 41 — the engine tail-limits, so that is m79-m99 and m59-m99.
    relay.results = [EngineResult(data=_messages(100)[-21:])]
    first = ok(
        client.get(
            f"{_base()}/{SESSION_ID}/messages", params={"page": 1, "page_size": 20}
        )
    )
    relay.results = [EngineResult(data=_messages(100)[-41:])]
    second = ok(
        client.get(
            f"{_base()}/{SESSION_ID}/messages", params={"page": 2, "page_size": 20}
        )
    )

    def ids(d):
        return [i["message_id"] for i in d["items"]]

    assert ids(first) == [f"m{i}" for i in range(80, 100)]
    assert ids(second) == [f"m{i}" for i in range(60, 80)]
    assert not set(ids(first)) & set(ids(second))


def test_the_newest_message_is_on_the_first_page(client, relay):
    """It used to be spent as the lookahead item and never shown."""
    relay.results = [EngineResult(data=_messages(100)[-21:])]
    data = ok(
        client.get(
            f"{_base()}/{SESSION_ID}/messages", params={"page": 1, "page_size": 20}
        )
    )
    assert data["items"][-1]["message_id"] == "m99"


def test_history_total_is_exact_once_the_tail_is_the_whole_history(client, relay):
    """A short tail proves nothing older exists, so the count is not a bound."""
    relay.results = [EngineResult(data=_messages(30))]
    data = ok(
        client.get(
            f"{_base()}/{SESSION_ID}/messages", params={"page": 2, "page_size": 20}
        )
    )
    assert data["total"] == 30
    assert [i["message_id"] for i in data["items"]] == [f"m{i}" for i in range(10)]


def test_history_total_is_a_floor_while_older_messages_remain(client, relay):
    relay.results = [EngineResult(data=_messages(100)[-41:])]
    data = ok(
        client.get(
            f"{_base()}/{SESSION_ID}/messages", params={"page": 2, "page_size": 20}
        )
    )
    assert data["total"] == 41


def test_paging_past_the_start_of_history_is_empty(client, relay):
    relay.results = [EngineResult(data=_messages(30))]
    data = ok(
        client.get(
            f"{_base()}/{SESSION_ID}/messages", params={"page": 3, "page_size": 20}
        )
    )
    assert data["items"] == []


def test_a_page_number_cannot_amplify_into_device_load(client, relay):
    """``page_size`` is capped at 100 but ``page`` is only ``ge=1``, and the
    tail-limited window grows with the page number. Unguarded, this asked a
    tenant's device for ~100M messages to answer with at most 100. The depth
    check now refuses it before the device is touched at all."""
    relay.results = [EngineResult(data=_messages(1))]
    fails(
        client.get(
            f"{_base()}/{SESSION_ID}/messages",
            params={"page": 1000000, "page_size": 100},
        ),
        422,
    )
    assert relay.calls == []


def test_the_cap_does_not_bite_within_the_served_depth(client, relay):
    """A page inside the documented depth still asks for exactly its window."""
    relay.results = [EngineResult(data=_messages(1))]
    ok(
        client.get(
            f"{_base()}/{SESSION_ID}/messages", params={"page": 10, "page_size": 100}
        )
    )
    assert relay.calls[0]["params"] == {"offset": 0, "limit": 1001}


def test_a_page_past_the_capped_depth_is_refused_not_served_empty(client, relay):
    """An empty page is this endpoint's end-of-history signal, so it cannot also
    mean "the cap stopped me" — that reported ``total=5001`` on a history of any
    size. Past the depth the request is refused instead."""
    relay.results = [EngineResult(data=_messages(5001))]
    fails(
        client.get(
            f"{_base()}/{SESSION_ID}/messages",
            params={"page": 1000, "page_size": 100},
        ),
        422,
    )


def test_the_page_landing_on_the_cap_is_refused(client, relay):
    """The boundary page, which a far-past-the-end page skips clean over.

    The clamped fetch is ``_MAX_HISTORY_DEPTH + 1`` long, so page 51 at
    ``page_size=100`` lands with exactly one item left in the window — the
    lookahead, which is never content. Suppressing it left an empty page
    reporting an exact ``total``; the page is refused instead.
    """
    relay.results = [EngineResult(data=_messages(5001))]
    fails(
        client.get(
            f"{_base()}/{SESSION_ID}/messages",
            params={"page": 51, "page_size": 100},
        ),
        422,
    )


def test_a_page_straddling_the_cap_is_refused(client, relay):
    """The bound is the window's *end*, not its start.

    Rejecting only ``offset >= depth`` would still admit this page: at
    ``page_size=3`` page 1667 starts at 4998, inside the depth, and returns two
    messages of three. Short — and therefore exact-looking — while 45 000 more
    messages exist behind the cap.
    """
    relay.results = [EngineResult(data=_messages(5001))]
    fails(
        client.get(
            f"{_base()}/{SESSION_ID}/messages",
            params={"page": 1667, "page_size": 3},
        ),
        422,
    )


def test_the_depth_refusal_says_what_the_caller_hit(client, relay):
    """The status alone is not actionable — ``page_size=101`` is also a 422.

    The message has to be a mapped one: an unmapped exception reaching an
    app-level handler is answered with the bare HTTP reason phrase, so a
    ``HTTPException`` here would have told the caller only "Unprocessable
    Entity" however well its detail was written.
    """
    body = fails(
        client.get(
            f"{_base()}/{SESSION_ID}/messages",
            params={"page": 51, "page_size": 100},
        ),
        422,
    )
    assert body["message"] == (
        "Requested page is deeper than the message history this endpoint serves"
    )


def test_the_page_just_inside_the_cap_is_still_served_whole(client, relay):
    """The guard must not cost a page the depth does cover: page 1666 at
    ``page_size=3`` ends exactly on 4998 and is a full three messages."""
    relay.results = [EngineResult(data=_messages(4999))]
    data = ok(
        client.get(
            f"{_base()}/{SESSION_ID}/messages",
            params={"page": 1666, "page_size": 3},
        )
    )
    assert len(data["items"]) == 3


def test_the_last_page_within_the_cap_is_still_whole(client, relay):
    """The floor must not eat into content the cap does cover: page 50 is the
    deepest served page and is a full 100 messages."""
    relay.results = [EngineResult(data=_messages(5001))]
    data = ok(
        client.get(
            f"{_base()}/{SESSION_ID}/messages",
            params={"page": 50, "page_size": 100},
        )
    )
    ids = [i["message_id"] for i in data["items"]]
    assert len(ids) == 100
    # Newest-first pages: page 50 is messages 4901..5000 counting back, which in
    # the fetched tail of 5001 is indices 1..100 — index 0 is the lookahead.
    assert ids == [f"m{i}" for i in range(1, 101)]


def test_an_uncapped_history_still_serves_its_final_short_page(client, relay):
    """The floor is inert below the cap — a genuinely short tail is unaffected."""
    relay.results = [EngineResult(data=_messages(30))]
    data = ok(
        client.get(
            f"{_base()}/{SESSION_ID}/messages", params={"page": 2, "page_size": 20}
        )
    )
    assert [i["message_id"] for i in data["items"]] == [f"m{i}" for i in range(10)]


def test_the_session_window_stays_page_sized(client, relay):
    """The session list paginates a materialised list, so ``limit`` there really
    is a page size and must not grow with the offset."""
    relay.results = [EngineResult(data=[ENGINE_SESSION])]
    ok(client.get(_base(), params={"page": 3, "page_size": 50}))
    assert relay.calls[0]["params"] == {"offset": 100, "limit": 51}


# ── the operator gate ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "suffix"),
    [
        ("get", ""),
        ("post", ""),
        ("get", f"/{SESSION_ID}"),
        ("patch", f"/{SESSION_ID}"),
        ("delete", f"/{SESSION_ID}"),
        ("get", f"/{SESSION_ID}/messages"),
        ("delete", f"/{SESSION_ID}/messages"),
        ("get", "/favorites"),
        ("put", f"/{SESSION_ID}/favorite"),
        ("delete", f"/{SESSION_ID}/favorite"),
    ],
)
def test_a_service_bot_is_served_and_addressed_at_its_draft_device(
    client, relay, method, suffix
):
    """All ten routes serve a service bot at its DRAFT binding by default.

    A request that names no stage must behave exactly as before stages were
    addressable: every forward carries ``stage == "draft"``, addressing the
    pre-publication workspace — never the published runtime the relay's
    internal default would resolve.
    """
    relay.set_bot_type("service")
    kwargs = {"json": {}} if method in ("post", "patch") else {}
    resp = getattr(client, method)(f"{_base()}{suffix}", **kwargs)
    assert resp.status_code in (200, 201), resp.json()
    assert relay.calls, "the route never forwarded"
    assert all(c["stage"] == "draft" for c in relay.calls)


def test_a_collaborators_request_is_served(make_client, relay):
    """The flip of the old shared-bot 501: a member-level collaborator now
    operates the bot — naming the owner they address — instead of sharing
    closing the surface for everyone."""
    relay.set_bot_type("service")
    relay.add_operator("u2")
    client = make_client(router, caller="u2")
    resp = client.get(_base(), params={"owner_id": OWNER})
    assert resp.status_code == 200, resp.json()
    assert relay.paths == ["/api/sessions"]


def test_a_non_operator_is_the_masked_404(make_client, relay):
    """A caller who is neither owner nor collaborator learns nothing — the
    answer is byte-identical to naming a bot that does not exist, and no
    forward is even attempted."""
    client = make_client(router, caller="u9")
    refused = fails(client.get(_base(), params={"owner_id": OWNER}), 404)
    absent = fails(client.get("/openapi/v1/bots/no-such-bot/sessions"), 404)
    assert refused == {**absent, "request_id": refused["request_id"]}
    assert relay.attempts == []


@pytest.mark.parametrize("bot_type", ["", "something-new"])
def test_an_unknown_bot_type_gets_501_without_touching_the_device(
    client, relay, bot_type
):
    """The gate is an allowlist — a type this build has never heard of is not
    silently treated as the permissive case."""
    relay.set_bot_type(bot_type)
    body = fails(client.get(_base()), 501)
    assert body["message"] == "Not supported for this bot type"
    assert relay.calls == []


@pytest.mark.parametrize(
    "method,suffix",
    [
        ("get", ""),
        ("post", ""),
        ("get", f"/{SESSION_ID}"),
        ("patch", f"/{SESSION_ID}"),
        ("delete", f"/{SESSION_ID}"),
        ("get", f"/{SESSION_ID}/messages"),
        ("delete", f"/{SESSION_ID}/messages"),
        ("get", "/favorites"),
        ("put", f"/{SESSION_ID}/favorite"),
        ("delete", f"/{SESSION_ID}/favorite"),
    ],
)
def test_a_collaborated_personal_bot_is_served_to_its_operators(
    make_client, relay, method, suffix
):
    """The flip of the old shared-personal 501, on all ten routes.

    A coding app's collaborators are its team: each of them — and the owner —
    operates the bot's workspace. The surface is an operator console, and the
    exposure is device-wide by documented contract; what changed is who may
    hold the console, adjudicated per caller instead of refused for all.
    """
    relay.add_operator("u2")
    client = make_client(router, caller="u2")
    kwargs = {"json": {}} if method in ("post", "patch") else {}
    kwargs["params"] = {"owner_id": OWNER}
    resp = getattr(client, method)(f"{_base()}{suffix}", **kwargs)
    assert resp.status_code in (200, 201), resp.json()
    assert relay.calls, "the route never forwarded"


def test_the_owners_own_request_is_still_served(client, relay):
    """The gate must not have closed the case it exists to allow: the owner,
    naming nothing extra, exactly as before the expansion."""
    assert client.get(_base()).status_code == 200
    assert relay.paths == ["/api/sessions"]


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


# ── BCN-authorized friend compatibility branch ─────────────────────────────


def test_friend_sessions_reuse_expert_chat_without_owner_runtime_relay(
    make_client, relay, friendships, expert
):
    caller = "friend-1"
    friendships.allowed = True
    expert.sessions = {
        "items": [{"id": "friend-session", "title": "From Expert Chat"}],
        "total": 1,
    }
    client = make_client(router, caller=caller)

    data = ok(client.get(_base(), params={"owner_id": OWNER}))

    assert data["total"] == 1
    assert data["items"][0]["session_id"] == "friend-session"
    assert relay.calls == []
    assert friendships.calls[0]["human_id"] == caller
    assert friendships.calls[0]["owner_id"] == OWNER
    assert [call[0] for call in expert.calls] == [
        "add_chat_bot",
        "list_chat_sessions",
    ]


def test_non_friend_keeps_the_existing_masked_not_found(
    make_client, relay, friendships, expert
):
    client = make_client(router, caller="stranger")

    assert fails(client.get(_base(), params={"owner_id": OWNER}), 404)[
        "message"
    ] == "Not found"
    assert relay.calls == []
    assert len(friendships.calls) == 1
    assert expert.calls == []


def test_owner_path_does_not_query_bcn_or_expert_chat(
    client, relay, friendships, expert
):
    assert client.get(_base()).status_code == 200
    assert relay.paths == ["/api/sessions"]
    assert friendships.calls == []
    assert expert.calls == []


@pytest.mark.parametrize(
    ("method", "suffix", "body", "expert_method"),
    [
        ("post", "", {"title": "Named"}, "create_chat_session"),
        ("get", "/friend-session", None, "get_owned_chat_session"),
        ("patch", "/friend-session", {"title": "Renamed"}, "update_owned_chat_session"),
        ("delete", "/friend-session", None, "delete_owned_chat_session"),
        ("get", "/friend-session/messages", None, "list_owned_chat_session_messages"),
        ("delete", "/friend-session/messages", None, "clear_owned_chat_session_messages"),
        ("get", "/favorites", None, "list_chat_sessions"),
        ("put", "/friend-session/favorite", None, "set_owned_chat_session_favorite"),
        ("delete", "/friend-session/favorite", None, "set_owned_chat_session_favorite"),
    ],
)
def test_each_friend_session_operation_reuses_expert_chat(
    make_client, friendships, expert, relay, method, suffix, body, expert_method
):
    friendships.allowed = True
    expert.sessions = {"items": [expert.session], "total": 1}
    client = make_client(router, caller="friend-1")
    kwargs = {"params": {"owner_id": OWNER}}
    if body is not None:
        kwargs["json"] = body

    response = getattr(client, method)(f"{_base()}{suffix}", **kwargs)

    assert response.status_code in (200, 201), response.json()
    assert expert_method in [call[0] for call in expert.calls]
    assert relay.calls == []


def test_friend_access_is_draft_only(make_client, friendships, expert):
    friendships.allowed = True
    client = make_client(router, caller="friend-1")

    response = client.get(
        _base(), params={"owner_id": OWNER, "stage": "online"}
    )

    assert fails(response, 404)["message"] == "Not found"
    assert friendships.calls == []
    assert expert.calls == []
