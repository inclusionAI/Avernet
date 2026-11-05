"""The platform-API chat transport, driven through a fake SSE server.

The fake server speaks the exact frames BaaS's ``DefaultStreamConverter``
emits — every chat chunk arrives as ``event: chat`` and is discriminated by
``data.state``; heartbeats arrive as SSE comment frames — so these tests pin the
parser against the real wire shape rather than an invented one.

What is pinned here, in the order a leg meets it:

- the request the transport sends (address, token, session semantics),
- the frames it accepts and the frames it ignores,
- how it terminates, and what it refuses to call a termination,
- the caller-owned deadline and cooperative cancellation,
- the failure classification for each HTTP status.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import httpx
import pytest

from agentclaw.community.core.gateway_principal import PrincipalSignerConfig
from agentclaw.community.plugins.community.agentswarm.chat.base import (
    ChatError,
    ChatRateLimitedError,
    ChatRefusedError,
    ChatTimeoutError,
    ChatTransport,
    ChatUnavailableError,
    SessionMode,
)
from agentclaw.community.plugins.community.agentswarm.chat.baas_gateway import (
    BaasGatewayChatTransport,
)

SIGNING_KEY = "k" * 64
SIGNER = PrincipalSignerConfig(
    signing_key=SIGNING_KEY, audience="baas", issuer="backend", key_id="bare"
)
BOT_ID = "agent_swarm_abc123"
OWNER = "owner-1"
TENANT = "teamclaw"
BASE_URL = "http://baas.test:31230"


# ── fakes ──────────────────────────────────────────────────────────────────


_UNSET = object()


class _BotRepo:
    def __init__(self, record: dict | None = _UNSET) -> None:
        # An explicit sentinel so "no row" is expressible: a plain ``None``
        # default would be replaced by the healthy record below.
        self.record = {"device_id": "dev-1"} if record is _UNSET else record

    def get_by_id_and_entity(self, bot_id, entity_id):
        return self.record


def _sse(*frames: str) -> str:
    return "".join(frames)


def chat_delta(text: str) -> str:
    return "event: chat\ndata: " + json.dumps({"state": "delta", "deltaText": text}) + "\n\n"


def chat_final(text: str | None = None) -> str:
    data: dict = {"state": "final"}
    if text is not None:
        data["message"] = {"role": "assistant", "content": [{"type": "text", "text": text}]}
    return "event: chat\ndata: " + json.dumps(data) + "\n\n"


def chat_error(message: str) -> str:
    return "event: chat\ndata: " + json.dumps({"state": "error", "errorMessage": message}) + "\n\n"


def chat_aborted() -> str:
    return "event: chat\ndata: " + json.dumps({"state": "aborted"}) + "\n\n"


HEARTBEAT = ": heartbeat\n\n"
AGENT_FRAME = 'event: agent\ndata: {"tool":"x"}\n\n'


def _transport_returning(
    body: str, *, status: int = 200, record: list | None = None
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if record is not None:
            record.append(request)
        return httpx.Response(status, text=body, headers={"content-type": "text/event-stream"})

    return httpx.MockTransport(handler)


def _make(transport, **kwargs) -> BaasGatewayChatTransport:
    return BaasGatewayChatTransport(
        bot_repo=_BotRepo(),
        base_url=BASE_URL,
        signer=SIGNER,
        transport=transport,
        **kwargs,
    )


# ── the request ────────────────────────────────────────────────────────────


class TestRequest:
    def test_posts_to_the_streaming_chat_endpoint(self):
        seen: list[httpx.Request] = []
        transport = _make(_transport_returning(chat_final("hi") + chat_final("hi"), record=seen))

        transport.chat(BOT_ID, "hello", owner_id=OWNER, tenant=TENANT)

        assert str(seen[0].url) == f"{BASE_URL}/openapi/v1/chat/stream"
        assert seen[0].method == "POST"

    def test_principal_is_sent_in_the_gateway_header(self):
        import jwt

        seen: list[httpx.Request] = []
        transport = _make(_transport_returning(chat_final("hi"), record=seen))

        transport.chat(BOT_ID, "hello", owner_id=OWNER, tenant=TENANT)

        # BaaS reads Header(alias="x-avernet-principal"); a bearer token here
        # answers 40101 "Token missing" instead.
        token = seen[0].headers["x-avernet-principal"]
        assert "authorization" not in seen[0].headers
        claims = jwt.decode(
            token, SIGNING_KEY, algorithms=["HS256"],
            options={"verify_aud": False},
        )
        principal = claims["principals"][0]
        assert principal["tenant"] == "team_claw"
        assert principal["access_key"]["access_key"] == "agent_swarm_agent_team_claw"

    def test_addresses_the_bot_under_its_owner(self):
        seen: list[httpx.Request] = []
        transport = _make(_transport_returning(chat_final("hi"), record=seen))

        transport.chat(BOT_ID, "hello", owner_id=OWNER, tenant=TENANT)

        assert json.loads(seen[0].content)["bot_id"] == f"{BOT_ID}:{OWNER}"

    def test_fresh_mode_carries_a_new_business_task_id(self):
        seen: list[httpx.Request] = []
        transport = _make(_transport_returning(chat_final("hi"), record=seen))

        transport.chat(BOT_ID, "a", owner_id=OWNER, tenant=TENANT,
                       session_mode=SessionMode.FRESH)
        transport.chat(BOT_ID, "b", owner_id=OWNER, tenant=TENANT,
                       session_mode=SessionMode.FRESH)

        first = json.loads(seen[0].content)["metadata"]
        second = json.loads(seen[1].content)["metadata"]
        assert "biz_task_id" in first and "biz_task_id" in second
        assert first["biz_task_id"] != second["biz_task_id"]
        assert "session_key" not in first

    def test_continue_mode_carries_the_session_key(self):
        seen: list[httpx.Request] = []
        transport = _make(_transport_returning(chat_final("hi"), record=seen))

        transport.chat(BOT_ID, "a", owner_id=OWNER, tenant=TENANT,
                       session_mode=SessionMode.CONTINUE, session_key="sess-9")

        metadata = json.loads(seen[0].content)["metadata"]
        assert metadata["session_key"] == "sess-9"
        assert "biz_task_id" not in metadata


# ── what the stream means ─────────────────────────────────────────────────


class TestStreamInterpretation:
    def test_returns_the_reply_text(self):
        transport = _make(_transport_returning(chat_final("hello world")))

        assert transport.chat(BOT_ID, "x", owner_id=OWNER, tenant=TENANT) == "hello world"

    def test_concatenates_incremental_deltas(self):
        body = _sse(chat_delta("hello "), chat_delta("brave "), chat_delta("world"),
                    chat_final())
        transport = _make(_transport_returning(body))

        assert transport.chat(BOT_ID, "x", owner_id=OWNER, tenant=TENANT) == "hello brave world"

    def test_final_body_wins_over_accumulated_deltas(self):
        body = _sse(chat_delta("partial"), chat_final("the complete answer"))
        transport = _make(_transport_returning(body))

        assert transport.chat(BOT_ID, "x", owner_id=OWNER, tenant=TENANT) == "the complete answer"

    def test_heartbeat_comment_frames_are_ignored(self):
        body = _sse(HEARTBEAT, chat_delta("hi"), HEARTBEAT, chat_final())
        transport = _make(_transport_returning(body))

        assert transport.chat(BOT_ID, "x", owner_id=OWNER, tenant=TENANT) == "hi"

    def test_non_chat_events_are_ignored(self):
        body = _sse(AGENT_FRAME, chat_delta("hi"), AGENT_FRAME, chat_final())
        transport = _make(_transport_returning(body))

        assert transport.chat(BOT_ID, "x", owner_id=OWNER, tenant=TENANT) == "hi"

    def test_empty_final_returns_empty_string(self):
        transport = _make(_transport_returning(chat_final()))

        assert transport.chat(BOT_ID, "x", owner_id=OWNER, tenant=TENANT) == ""

    def test_stream_without_final_is_not_a_success(self):
        # A dropped connection looks exactly like a finished short answer; only
        # the final frame distinguishes them.
        transport = _make(_transport_returning(chat_delta("half an ans")))

        with pytest.raises(ChatError, match="without a terminal frame"):
            transport.chat(BOT_ID, "x", owner_id=OWNER, tenant=TENANT)

    def test_error_frame_fails_the_leg(self):
        transport = _make(_transport_returning(chat_error("model exploded")))

        with pytest.raises(ChatRefusedError, match="model exploded"):
            transport.chat(BOT_ID, "x", owner_id=OWNER, tenant=TENANT)

    def test_aborted_frame_fails_the_leg(self):
        transport = _make(_transport_returning(chat_aborted()))

        with pytest.raises(ChatRefusedError, match="aborted"):
            transport.chat(BOT_ID, "x", owner_id=OWNER, tenant=TENANT)

    def test_malformed_data_line_is_skipped(self):
        body = "event: chat\ndata: {not json\n\n" + chat_final("ok")
        transport = _make(_transport_returning(body))

        assert transport.chat(BOT_ID, "x", owner_id=OWNER, tenant=TENANT) == "ok"


# ── deadline and cancellation ─────────────────────────────────────────────


class TestDeadlineAndCancellation:
    def test_exceeding_the_deadline_raises_timeout(self):
        transport = _make(_blocking_transport(), recv_slice_seconds=0.01)

        with pytest.raises(ChatTimeoutError, match="did not finish"):
            transport.chat(BOT_ID, "x", owner_id=OWNER, tenant=TENANT, timeout_seconds=0.05)

    def test_caller_deadline_shorter_than_default_is_honoured(self):
        transport = _make(_blocking_transport(), recv_slice_seconds=0.01)
        started = time.monotonic()

        with pytest.raises(ChatTimeoutError):
            transport.chat(BOT_ID, "x", owner_id=OWNER, tenant=TENANT, timeout_seconds=0.05)

        assert time.monotonic() - started < 2.0

    def test_cancellation_returns_empty_and_closes(self):
        cancel = threading.Event()
        setter = threading.Thread(target=lambda: (time.sleep(0.05), cancel.set()))
        setter.start()
        transport = _make(_blocking_transport(), recv_slice_seconds=0.01)

        reply = transport.chat(BOT_ID, "x", owner_id=OWNER, tenant=TENANT,
                               cancel_event=cancel, timeout_seconds=5.0)
        setter.join(timeout=1)

        assert reply == ""
        assert cancel.is_set()

    def test_cancellation_issues_no_abort_request(self):
        seen: list[httpx.Request] = []
        cancel = threading.Event()
        setter = threading.Thread(target=lambda: (time.sleep(0.05), cancel.set()))
        setter.start()
        transport = _make(_blocking_transport(seen=seen), recv_slice_seconds=0.01)

        transport.chat(BOT_ID, "x", owner_id=OWNER, tenant=TENANT,
                       cancel_event=cancel, timeout_seconds=5.0)
        setter.join(timeout=1)

        # Exactly one request: the stream itself. No abort call follows it.
        assert len(seen) == 1


def _blocking_transport(seen: list | None = None) -> httpx.AsyncBaseTransport:
    """A transport whose SSE body never terminates.

    ``httpx.MockTransport`` only implements the synchronous half of the
    transport protocol, so a streamed async response through it is drained
    eagerly and a "hung" stream would finish instantly. A real
    ``AsyncBaseTransport`` keeps the body open, which is the only state these
    tests can meaningfully assert against.
    """
    return _BlockingTransport(seen)


class _BlockingTransport(httpx.AsyncBaseTransport):
    def __init__(self, seen: list | None = None) -> None:
        self._seen = seen

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self._seen is not None:
            self._seen.append(request)
        return httpx.Response(
            200,
            stream=_BlockingSseStream(),
            headers={"content-type": "text/event-stream"},
        )


class _BlockingSseStream(httpx.AsyncByteStream):
    """Endless heartbeat frames, one every 10ms.

    ``aiter_bytes`` is implemented directly rather than relying on the base
    class's default: ``aiter_lines`` reads through the byte iterator, and the
    default path buffers the whole body before yielding anything.
    """

    def __aiter__(self):
        return self.aiter_bytes()

    async def aiter_bytes(self):
        while True:
            yield b": heartbeat\n\n"
            await asyncio.sleep(0.01)


# ── failure classification ────────────────────────────────────────────────


class TestHTTPFailureClassification:
    @pytest.mark.parametrize("status", [401, 404, 503])
    def test_unavailable_statuses(self, status):
        transport = _make(_transport_returning("", status=status))

        with pytest.raises(ChatUnavailableError):
            transport.chat(BOT_ID, "x", owner_id=OWNER, tenant=TENANT)

    def test_forbidden_is_refused(self):
        transport = _make(_transport_returning("", status=403))

        with pytest.raises(ChatRefusedError, match="403"):
            transport.chat(BOT_ID, "x", owner_id=OWNER, tenant=TENANT)

    def test_rate_limited(self):
        transport = _make(_transport_returning("", status=429))

        with pytest.raises(ChatRateLimitedError):
            transport.chat(BOT_ID, "x", owner_id=OWNER, tenant=TENANT)

    def test_other_statuses_are_base_errors(self):
        transport = _make(_transport_returning("", status=418))

        with pytest.raises(ChatError) as excinfo:
            transport.chat(BOT_ID, "x", owner_id=OWNER, tenant=TENANT)
        assert type(excinfo.value) is ChatError
        assert "418" in str(excinfo.value)

    def test_transport_error_is_unavailable(self):
        def handler(request):
            raise httpx.ConnectError("no route")

        transport = _make(httpx.MockTransport(handler))

        with pytest.raises(ChatUnavailableError, match="failed"):
            transport.chat(BOT_ID, "x", owner_id=OWNER, tenant=TENANT)


# ── preconditions and tenant resolution ──────────────────────────────────


class TestPreconditions:
    def test_backend_tenant_is_mapped_to_the_platform_spelling(self):
        """The two sides name one tenant differently; the leg sends theirs.

        The backend isolates data under ``teamclaw``, the platform's tables and
        API use ``team_claw``. The platform reads the tenant from the minted
        principal, so sending the backend's spelling makes it address a tenant
        that owns no bots.
        """
        import jwt

        seen: list[httpx.Request] = []
        transport = _make(_transport_returning(chat_final("hi"), record=seen))

        transport.chat(BOT_ID, "x", owner_id=OWNER, tenant="teamclaw")

        token = seen[0].headers["x-avernet-principal"]
        claims = jwt.decode(
            token, SIGNING_KEY, algorithms=["HS256"], options={"verify_aud": False}
        )
        principal = claims["principals"][0]
        assert principal["tenant"] == "team_claw"
        assert principal["access_key"]["access_key"] == "agent_swarm_agent_team_claw"

    def test_every_leg_addresses_the_platform_tenant(self):
        """The platform has one tenant; a caller's spelling does not change it.

        Only the backend's ``teamclaw`` and the platform's ``team_claw`` name
        the same tenant, so the leg always mints the platform spelling — a
        caller-supplied value selects nothing and must not reach the principal
        the resource key is looked up by.
        """
        import jwt

        seen: list[httpx.Request] = []
        transport = _make(_transport_returning(chat_final("hi"), record=seen))

        transport.chat(BOT_ID, "x", owner_id=OWNER, tenant="other")

        token = seen[0].headers["x-avernet-principal"]
        claims = jwt.decode(
            token, SIGNING_KEY, algorithms=["HS256"], options={"verify_aud": False}
        )
        assert claims["principals"][0]["tenant"] == "team_claw"
        assert (
            claims["principals"][0]["access_key"]["access_key"]
            == "agent_swarm_agent_team_claw"
        )

    def test_blank_owner_is_refused(self):
        transport = _make(_transport_returning(chat_final("never sent")))

        with pytest.raises(ChatRefusedError, match="authenticated owner"):
            transport.chat(BOT_ID, "x", owner_id="  ", tenant=TENANT)

    def test_unknown_bot_is_unavailable(self):
        transport = BaasGatewayChatTransport(
            bot_repo=_BotRepo(record=None), base_url=BASE_URL, signer=SIGNER,
            transport=_transport_returning(chat_final("x")),
        )

        with pytest.raises(ChatUnavailableError, match="not found"):
            transport.chat(BOT_ID, "x", owner_id=OWNER, tenant=TENANT)

    def test_bot_without_runtime_binding_is_unavailable(self):
        transport = BaasGatewayChatTransport(
            bot_repo=_BotRepo(record={"device_id": ""}), base_url=BASE_URL,
            signer=SIGNER,             transport=_transport_returning(chat_final("x")),
        )

        with pytest.raises(ChatUnavailableError, match="no runtime binding"):
            transport.chat(BOT_ID, "x", owner_id=OWNER, tenant=TENANT)

    def test_blank_tenant_still_addresses_the_platform_tenant(self):
        seen: list[httpx.Request] = []
        transport = _make(_transport_returning(chat_final("hi"), record=seen))

        transport.chat(BOT_ID, "x", owner_id=OWNER, tenant="   ")

        import jwt

        token = seen[0].headers["x-avernet-principal"]
        claims = jwt.decode(token, SIGNING_KEY, algorithms=["HS256"],
                            options={"verify_aud": False})
        assert claims["principals"][0]["tenant"] == "team_claw"

    def test_unusable_signing_key_is_unavailable(self):
        transport = BaasGatewayChatTransport(
            bot_repo=_BotRepo(), base_url=BASE_URL,
            signer=PrincipalSignerConfig(
                signing_key="", audience="baas", issuer="backend", key_id="bare"
            ),
            transport=_transport_returning(chat_final("x")),
        )

        with pytest.raises(ChatUnavailableError, match="cannot authenticate"):
            transport.chat(BOT_ID, "x", owner_id=OWNER, tenant=TENANT)


class TestSeamConformance:
    def test_is_a_chat_transport(self):
        assert isinstance(_make(_transport_returning(chat_final("x"))), ChatTransport)

    def test_reports_the_configured_default_deadline(self):
        transport = _make(_transport_returning(chat_final("x")), default_timeout=42.0)

        assert transport.chat_timeout_seconds == 42.0

    def test_trailing_slash_on_base_url_is_normalised(self):
        seen: list[httpx.Request] = []
        transport = BaasGatewayChatTransport(
            bot_repo=_BotRepo(), base_url=BASE_URL + "/", signer=SIGNER,
                        transport=_transport_returning(chat_final("x"), record=seen),
        )

        transport.chat(BOT_ID, "x", owner_id=OWNER, tenant=TENANT)

        assert str(seen[0].url) == f"{BASE_URL}/openapi/v1/chat/stream"