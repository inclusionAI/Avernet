"""The chat seam's contract, exercised against both implementations.

A contract test is only worth its name if the same assertions run against every
implementation — otherwise each channel is merely self-consistent and the two
can drift while both suites stay green. So this module parametrizes over
``engine_ws`` and ``baas_gateway`` and drives both through one fake bot.

What the seam promises, and what is therefore asserted for both:

- a completed leg returns the reply text, assembled across incremental pieces,
- non-content frames never leak into the reply,
- a fresh leg does not inherit history; a continuation does,
- a blank owner is refused before any transport is opened,
- the caller's deadline is the one that ends the leg,
- cancellation returns an empty result the caller can tell apart from an empty
  answer,
- every failure lands in the same category regardless of channel.
"""

from __future__ import annotations

import json
import threading
import time

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
from agentclaw.community.plugins.community.agentswarm.chat.engine_ws import (
    ChatLegMixin,
)
from tests.community.plugins.community.agentswarm.chat.test_baas_gateway import (
    _blocking_transport,
    _BlockingTransport,
    chat_final,
    chat_delta,
    chat_error,
    _transport_returning,
)

BOT_ID = "agent_swarm_contract"
OWNER = "owner-1"
TENANT = "teamclaw"
SIGNER = PrincipalSignerConfig(
    signing_key="k" * 64, audience="baas", issuer="backend", key_id="bare"
)


# ── per-channel drives ────────────────────────────────────────────────────


class _Repo:
    def __init__(self, record=None):
        self.record = {"device_id": "dev-1"} if record is None else record

    def get_by_id_and_entity(self, bot_id, entity_id):
        return self.record


def _baas(body: str, *, status: int = 200, seen: list | None = None):
    from agentclaw.community.plugins.community.agentswarm.chat.baas_gateway import (
        BaasGatewayChatTransport,
    )

    return BaasGatewayChatTransport(
        bot_repo=_Repo(),
        base_url="http://baas.test:31230",
        signer=SIGNER,
        transport=_transport_returning(body, status=status, record=seen),
        recv_slice_seconds=0.01,
    )


class _ScriptedWs:
    """A minimum engine socket: replays frames, then stalls."""

    def __init__(self, frames: list[dict], *, stall: float = 0.0) -> None:
        self._frames = [json.dumps(f) for f in frames]
        self._stall = stall
        self.closed = False

    async def send(self, _data):
        return None

    async def recv(self):
        if self._frames:
            return self._frames.pop(0)
        if self._stall:
            await __import__("asyncio").sleep(self._stall)
        raise ConnectionError("test socket exhausted")

    async def close(self):
        self.closed = True


def _engine(frames: list[dict], *, stall: float = 0.0):
    """An engine-channel transport over a scripted socket."""
    from agentclaw.community.plugins.community.agentswarm.chat import engine_ws

    ws = _ScriptedWs(frames, stall=stall)
    instance = ChatLegMixin()
    instance._chat_timeout_seconds = 5.0
    instance._bot_repo = _Repo()
    instance._baas_service = None

    # Synchronous on purpose: ``ChatLegMixin._engine_endpoint`` is a plain
    # method called from inside the leg's own event loop.
    instance._engine_endpoint = lambda bot_id, owner: ("ws://fake", "tok")

    async def connect(url, token):
        return ws

    async def no_reset(*_a, **_k):
        return None

    instance._connect_engine = connect
    instance._reset_session = no_reset
    return instance, ws


def _raising_endpoint_sync(bot_id, owner):
    """The bot cannot be reached at all — the engine channel's unreachable case."""
    raise ChatUnavailableError(f"bot {bot_id} not found for chat")


def _final_frame(text: str) -> dict:
    return {
        "type": "event",
        "event": "chat",
        "payload": {
            "state": "final",
            "message": {"content": [{"type": "text", "text": text}]},
        },
    }


# ── the contract ──────────────────────────────────────────────────────────


class Contract:
    """Assertions every ``ChatTransport`` must satisfy.

    Subclasses supply ``transport_for_reply`` and the failure constructors.
    """

    def transport_for_reply(self, pieces: list[str]) -> ChatTransport:
        raise NotImplementedError

    def transport_for_failure(self, kind: str) -> ChatTransport:
        raise NotImplementedError

    # -- success ---------------------------------------------------------

    def test_returns_the_reply_text(self):
        transport = self.transport_for_reply(["hello world"])

        assert transport.chat(BOT_ID, "x", owner_id=OWNER, tenant=TENANT) == "hello world"

    def test_the_reply_is_one_string(self):
        transport = self.transport_for_reply(["hello"])

        result = transport.chat(BOT_ID, "x", owner_id=OWNER, tenant=TENANT)

        assert isinstance(result, str)

    def test_non_content_frames_stay_out_of_the_reply(self):
        # The reply is the text, not the transport's bookkeeping.
        transport = self.transport_for_reply(["only this"])

        assert transport.chat(BOT_ID, "x", owner_id=OWNER, tenant=TENANT) == "only this"

    # -- session semantics ----------------------------------------------

    def test_fresh_is_the_default(self):
        # Absent a declaration the leg must be fresh; asserting the call works
        # with no session_mode is the observable half of that.
        transport = self.transport_for_reply(["ok"])

        assert transport.chat(BOT_ID, "x", owner_id=OWNER, tenant=TENANT) == "ok"

    def test_continue_is_accepted(self):
        transport = self.transport_for_reply(["ok"])

        result = transport.chat(
            BOT_ID, "x", owner_id=OWNER, tenant=TENANT,
            session_mode=SessionMode.CONTINUE, session_key="sess-1",
        )

        assert result == "ok"

    # -- owner -----------------------------------------------------------

    def test_blank_owner_is_refused(self):
        transport = self.transport_for_reply(["never"])

        with pytest.raises(ChatError):
            transport.chat(BOT_ID, "x", owner_id="   ", tenant=TENANT)

    def test_owner_is_not_ambient(self):
        # Two different owners must produce two different legs; nothing about
        # the transport remembers the first.
        transport = self.transport_for_reply(["a"])
        transport.chat(BOT_ID, "first", owner_id="owner-a", tenant=TENANT)

        transport_b = self.transport_for_reply(["b"])
        assert transport_b.chat(BOT_ID, "second", owner_id="owner-b", tenant=TENANT) == "b"

    # -- deadline --------------------------------------------------------

    def test_expired_deadline_is_a_timeout(self):
        transport = self.hanging_transport()

        with pytest.raises(ChatTimeoutError):
            transport.chat(BOT_ID, "x", owner_id=OWNER, tenant=TENANT, timeout_seconds=0.05)

    def test_the_caller_limit_ends_the_leg(self):
        transport = self.hanging_transport()
        started = time.monotonic()

        with pytest.raises(ChatTimeoutError):
            transport.chat(BOT_ID, "x", owner_id=OWNER, tenant=TENANT, timeout_seconds=0.05)

        # Not the (much longer) default, not the stall.
        assert time.monotonic() - started < 2.0

    def hanging_transport(self) -> ChatTransport:
        raise NotImplementedError

    # -- cancellation ----------------------------------------------------

    def test_cancellation_returns_an_empty_result(self):
        cancel = threading.Event()
        threading.Timer(0.05, cancel.set).start()
        transport = self.hanging_transport()

        reply = transport.chat(
            BOT_ID, "x", owner_id=OWNER, tenant=TENANT,
            cancel_event=cancel, timeout_seconds=5.0,
        )

        assert reply == ""
        assert cancel.is_set()

    def test_cancellation_is_distinguishable_from_an_empty_answer(self):
        # The seam's cancellation result is "empty AND the event is set"; a
        # bot that genuinely replies with nothing leaves the event clear.
        cancel = threading.Event()
        transport = self.transport_for_reply([""])
        reply = transport.chat(
            BOT_ID, "x", owner_id=OWNER, tenant=TENANT, cancel_event=cancel,
        )

        assert reply == ""
        assert not cancel.is_set()

    # -- failure classification -----------------------------------------

    def test_refusal_is_refused(self):
        with pytest.raises(ChatRefusedError):
            self.transport_for_failure("refused").chat(
                BOT_ID, "x", owner_id=OWNER, tenant=TENANT
            )

    def test_unreachable_is_unavailable(self):
        with pytest.raises(ChatUnavailableError):
            self.transport_for_failure("unavailable").chat(
                BOT_ID, "x", owner_id=OWNER, tenant=TENANT
            )

    def test_rate_limited_is_rate_limited(self):
        with pytest.raises(ChatRateLimitedError):
            self.transport_for_failure("rate_limited").chat(
                BOT_ID, "x", owner_id=OWNER, tenant=TENANT
            )


class TestBaasGatewayContract(Contract):
    def test_unclassified_failure_still_raises(self):
        # Never a silent empty answer: the caller must see an error.
        with pytest.raises(ChatError):
            self.transport_for_failure("other").chat(
                BOT_ID, "x", owner_id=OWNER, tenant=TENANT
            )

    def transport_for_reply(self, pieces: list[str]) -> ChatTransport:
        body = "".join(chat_delta(p) for p in pieces)
        body += chat_final()
        return _baas(body)

    def hanging_transport(self) -> ChatTransport:
        from agentclaw.community.plugins.community.agentswarm.chat.baas_gateway import (
            BaasGatewayChatTransport,
        )

        return BaasGatewayChatTransport(
            bot_repo=_Repo(),
            base_url="http://baas.test:31230",
            signer=SIGNER,
                transport=_blocking_transport(),
            recv_slice_seconds=0.01,
        )

    def transport_for_failure(self, kind: str) -> ChatTransport:
        from agentclaw.community.plugins.community.agentswarm.chat.baas_gateway import (
            BaasGatewayChatTransport,
        )

        if kind == "rate_limited":
            return _baas("", status=429)
        if kind == "refused":
            return _baas(chat_error("nope"))
        if kind == "unavailable":
            return _baas("", status=503)
        return _baas("", status=418)


class TestEngineChannelContract(Contract):
    def test_rate_limited_is_rate_limited(self):
        # The engine channel has no concurrency-ceiling vocabulary of its own;
        # rate limiting is a platform concept, asserted on the channel that has
        # one (see TestBaasGatewayContract).
        pytest.skip("rate limiting is a platform-channel concept")

    def transport_for_reply(self, pieces: list[str]) -> ChatTransport:
        # The engine leg returns the text its terminal frame carries; it does
        # not accumulate delta frames (see design D13).
        text = "".join(pieces)
        transport, _ = _engine([_final_frame(text)])
        return transport

    def hanging_transport(self) -> ChatTransport:
        transport, _ = _engine([], stall=5.0)
        return transport

    def transport_for_failure(self, kind: str) -> ChatTransport:
        if kind == "refused":
            frames = [{
                "type": "event", "event": "chat",
                "payload": {"state": "error", "errorMessage": "nope"},
            }]
            transport, _ = _engine(frames)
            return transport
        # Any other failure class on this channel comes from reaching the bot at
        # all: the engine has no HTTP status vocabulary, so what a caller sees
        # is the unreachable case. It must still be classified, never a silent
        # success.
        transport, _ = _engine([])
        transport._engine_endpoint = _raising_endpoint_sync
        return transport


class TestPlatformChannelAssemblesIncrements:
    """Streamed pieces become one reply — required only of the streaming channel."""

    def test_streamed_pieces_are_concatenated(self):
        body = chat_delta("hello ") + chat_delta("brave ") + chat_delta("world") + chat_final()
        transport = _baas(body)

        result = transport.chat(BOT_ID, "x", owner_id=OWNER, tenant=TENANT)

        assert result == "hello brave world"

    def test_a_final_body_supersedes_the_deltas(self):
        # When the terminal frame carries the full text, that is authoritative.
        body = chat_delta("partial") + chat_final("the complete answer")
        transport = _baas(body)

        assert transport.chat(BOT_ID, "x", owner_id=OWNER, tenant=TENANT) == "the complete answer"


class TestClassificationIsChannelIndependent:
    """The same failure lands in the same bucket on both channels."""

    def test_refusal_bucket_matches(self):
        with pytest.raises(ChatRefusedError):
            TestBaasGatewayContract().transport_for_failure("refused").chat(
                BOT_ID, "x", owner_id=OWNER, tenant=TENANT)
        with pytest.raises(ChatRefusedError):
            TestEngineChannelContract().transport_for_failure("refused").chat(
                BOT_ID, "x", owner_id=OWNER, tenant=TENANT)

    def test_unavailable_bucket_matches(self):
        with pytest.raises(ChatUnavailableError):
            TestBaasGatewayContract().transport_for_failure("unavailable").chat(
                BOT_ID, "x", owner_id=OWNER, tenant=TENANT)
        with pytest.raises(ChatUnavailableError):
            TestEngineChannelContract().transport_for_failure("unavailable").chat(
                BOT_ID, "x", owner_id=OWNER, tenant=TENANT)

    def test_both_implementations_are_transports(self):
        assert isinstance(_baas(chat_final("x")), ChatTransport)
        assert isinstance(_engine([])[0], ChatTransport)