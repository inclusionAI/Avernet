"""AvernetBotPlatform unit tests — the host adapter's contract, branch by branch.

Hand fakes for every dependency (no MagicMock, per house style); the create
leg records what the with-manifest submit would have received (patched at
``create_flow`` — the adapter imports it call-local, so the module-level
attribute patch is exactly what the call sees). The status table is pinned
branch for branch against the narrow mapping's documented vocabulary; the
chat leg exercises the real frame protocol over a scripted engine websocket
(``_patch_engine_routes`` swaps only the two container-provided edges —
endpoint resolution and the ws handshake), including the per-attempt
sessionKey addressing and the cooperative-cancel receive slices.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import types

import pytest

import agentclaw.community.plugins.community.agentswarm.host as host_module
from agentclaw.community.core.agentswarm.domain.models import (
    BotCreateSpec, BotState,
)
from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    ApplyStatus,
)
from agentclaw.community.core.bot_config_manifest.creation import (
    CREATE_ON_CONTAINER_TRIGGER,
)
from agentclaw.community.core.task_queue.types import (
    TaskStatus,
)
import agentclaw.community.core.bot_management.create_flow as create_flow
from agentclaw.community.plugins.community.agentswarm.host import (
    AvernetBotPlatform,
)

CREATE_FLOW_SUBMIT = (
    "agentclaw.community.core.bot_management.create_flow"
    ".submit_bot_creation_with_manifest"
)



class _Seam:
    def __init__(self):
        self.jobs: dict = {}
        self.discards: list = []

    def find_job(self, *, entity_id, bot_id):
        return self.jobs.get(bot_id)

    def discard(self, *, entity_id, bot_id, owner_id=None):
        self.discards.append((bot_id, owner_id))
        return True


class _Apply:
    def __init__(self, report=None):
        self.report = report

    def last_apply(self, *, entity_id, bot_id):
        return self.report


class _Repo:
    def __init__(self, bot=None):
        self.bot = bot

    def get_by_id_and_entity(self, bot_id, entity_id):
        return self.bot


class _BotSvc:
    def __init__(self):
        self.deleted: list = []

    def delete_bot(self, bot_id, user_id, nick_name=None):
        self.deleted.append((bot_id, user_id))
        return True


class _Space:
    def resolve_current(self, owner_id, header_space_id=None):
        return types.SimpleNamespace(kind="personal", numeric_id=None)


class _Baas:
    """Stand-in for the ws-info resolution: never reached in these tests
    (the endpoint pair is patched), exactly like the real service when the
    bot's runtime binding is scripted."""

    def get_ws_info_by_bot_uuid(self, *, bot_uuid, port, path):
        raise AssertionError("ws-info must not be resolved in unit fakes")


class _ScriptedEngineWs:
    """Fake engine websocket speaking the adapter's frame protocol: captures
    every chat.send request (its sessionKey + message), then answers with a
    scripted final event. ``recv`` optionally stalls, faking a hung engine."""

    def __init__(self, reply="final answer", stall_seconds=0.0):
        self.reply = reply
        self.stall_seconds = stall_seconds
        self.sent_frames: list[dict] = []
        self.closed = False

    async def send(self, raw):
        frame = json.loads(raw)
        self.sent_frames.append(frame)

    async def recv(self):
        if self.stall_seconds:
            await asyncio.sleep(self.stall_seconds)
        message = (self.sent_frames and self.sent_frames[-1]) or {}
        request_id = str(message.get("id") or "req")
        return json.dumps({
            "type": "event",
            "event": "chat",
            "payload": {
                "state": "final",
                "message": {"content": [{"type": "text", "text": self.reply}]},
                "id": request_id,
            },
        })

    async def close(self):
        self.closed = True


def _job(status, last_error=None):
    return types.SimpleNamespace(status=status, last_error=last_error)


def _mk(config=None, seam=None, apply_=None, repo=None, bot_svc=None,
        bridge_workers=None):
    return AvernetBotPlatform(
        owner_id="owner-1",
        manifest_seam=seam or _Seam(),
        bot_service=bot_svc or _BotSvc(),
        passport_plugin=types.SimpleNamespace(),
        skill_set_factory=types.SimpleNamespace(),
        bot_repo=repo or _Repo(),
        apply_service=apply_ or _Apply(),
        space_context=_Space(),
        baas_service=_Baas(),
        max_platform_workers=bridge_workers or 4,
    )


def _patch_engine_routes(monkeypatch, ws):
    """Point the real chat leg at ``ws``: endpoint resolution and the
    engine connect handshake are the two container-provided edges; the
    frame protocol (send/recv/accumulate) stays the production code."""
    monkeypatch.setattr(
        AvernetBotPlatform, "_engine_endpoint",
        lambda self, bot_id, owner: ("ws://fake", "tok"),
    )

    async def _connect(self, ws_url, token):
        return ws

    monkeypatch.setattr(AvernetBotPlatform, "_connect_engine", _connect)


# ── config validation ────────────────────────────────────────────────────


class TestCreate:

    def _record_submit(self, monkeypatch):
        seen: list = []

        def fake_submit(**kwargs):
            seen.append(kwargs)
            return types.SimpleNamespace(bot_id=kwargs["bot_id"])

        monkeypatch.setattr(create_flow, "submit_bot_creation_with_manifest",
                            fake_submit)
        return seen

    def test_identity_is_deterministic_per_idempotency_key(self, monkeypatch):
        seen = self._record_submit(monkeypatch)
        platform = _mk()
        spec = BotCreateSpec(name="scout", description="a surveyor")
        first = platform.create_bot(spec=spec, idempotency_key="run-1:scout")
        again = platform.create_bot(spec=spec, idempotency_key="run-1:scout")
        assert first == again and first.startswith("ac_") and len(first) == 19
        assert [w["bot_id"] for w in seen] == [first, first]

    def test_submit_receives_the_persona_not_inventions(self, monkeypatch):
        seen = self._record_submit(monkeypatch)
        platform = _mk()
        spec = BotCreateSpec(
            name="clerk", description="a filing clerk",
            instructions="File weekly reports.",
        )
        platform.create_bot(spec=spec, idempotency_key="run-2:clerk")
        (w,) = seen
        assert w["user_id"] == "owner-1" and w["modifier"] == "owner-1"
        document = w["document"]
        assert "schema_version: 1" in document
        assert "type: SOUL.md" in document
        assert "File weekly reports." in document
        host_spec = w["spec"]
        assert host_spec.bot_name == f"clerk-{first8(w['bot_id'][3:])}"
        assert host_spec.engine_type == "openclaw"
        assert host_spec.bot_desc == "a filing clerk"
        assert w["context"].deployment_mode.value == "cloud"
        assert document.count("template_display") == 0

    def test_empty_instructions_stay_empty_and_visible(self, monkeypatch):
        seen = self._record_submit(monkeypatch)
        platform = _mk()
        spec = BotCreateSpec(name="blank")
        platform.create_bot(spec=spec, idempotency_key="run-3:blank")
        document = seen[0]["document"]
        assert "type: SOUL.md" in document
        # empty content is the value ("no persona text"), not a filler default
        assert "content: ''" in document
        assert "Complete the assigned task" not in document

    def test_different_idempotency_keys_get_different_identities(
            self, monkeypatch):
        seen = self._record_submit(monkeypatch)
        platform = _mk()
        spec = BotCreateSpec(name="scout")
        platform.create_bot(spec=spec, idempotency_key="run-a:scout")
        platform.create_bot(spec=spec, idempotency_key="run-b:scout")
        names = {w["spec"].bot_name for w in seen}
        ids = {w["bot_id"] for w in seen}
        assert len(names) == 2 and len(ids) == 2


def first8(hexstr):
    return hexstr[:8]


# ── status: the narrow table ─────────────────────────────────────────────


class TestStatusNarrowTable:

    def test_no_job_answer_is_provisioning(self):
        platform = _mk()
        assert platform.bot_status("ac_unknown") == BotState.PROVISIONING.value

    def test_live_job_windows_are_provisioning(self):
        for status in (TaskStatus.PENDING, TaskStatus.RUNNING):
            seam = _Seam()
            seam.jobs["b1"] = _job(status)
            assert _mk(seam=seam).bot_status("b1") == BotState.PROVISIONING.value

    def test_terminal_job_without_bot_maps_the_jobs_own_answer(self):
        cases = [
            (TaskStatus.TIMED_OUT, None, BotState.AUTHORIZATION_EXPIRED.value),
            (TaskStatus.FAILED, "the authorization window elapsed",
             BotState.AUTHORIZATION_EXPIRED.value),
            (TaskStatus.FAILED, "the bot could not be provisioned: quota",
             BotState.CREATE_FAILED.value),
            (TaskStatus.FAILED, "declined by user",
             BotState.AUTHORIZATION_REJECTED.value),
        ]
        for status, err, want in cases:
            seam = _Seam()
            seam.jobs["b1"] = _job(status, err)
            assert _mk(seam=seam).bot_status("b1") == want, (status, err)

    def test_dead_bot_is_create_failed_even_with_live_job(self):
        seam = _Seam()
        seam.jobs["b1"] = _job(TaskStatus.SUCCEEDED)
        repo = _Repo(bot={"status": "FAILED"})
        assert _mk(seam=seam, repo=repo).bot_status("b1") == BotState.CREATE_FAILED.value

    def test_report_success_with_container_up_is_ready(self):
        seam = _Seam()
        seam.jobs["b1"] = _job(TaskStatus.RUNNING)
        repo = _Repo(bot={"status": "ACTIVE"})
        apply_ = _Apply(types.SimpleNamespace(
            trigger=CREATE_ON_CONTAINER_TRIGGER, status=ApplyStatus.SUCCEEDED))
        assert _mk(seam=seam, repo=repo, apply_=apply_).bot_status(
            "b1") == BotState.READY.value

    def test_report_success_with_container_coming_is_provisioning(self):
        # record-first engines apply before the container exists
        seam = _Seam()
        seam.jobs["b1"] = _job(TaskStatus.RUNNING)
        repo = _Repo(bot={"status": "PENDING"})
        apply_ = _Apply(types.SimpleNamespace(
            trigger=CREATE_ON_CONTAINER_TRIGGER, status=ApplyStatus.SUCCEEDED))
        assert _mk(seam=seam, repo=repo, apply_=apply_).bot_status(
            "b1") == BotState.PROVISIONING.value

    def test_failed_creation_report_is_apply_failed(self):
        seam = _Seam()
        seam.jobs["b1"] = _job(TaskStatus.RUNNING)
        repo = _Repo(bot={"status": "ACTIVE"})
        apply_ = _Apply(types.SimpleNamespace(
            trigger=CREATE_ON_CONTAINER_TRIGGER, status=ApplyStatus.FAILED))
        assert _mk(seam=seam, repo=repo, apply_=apply_).bot_status(
            "b1") == BotState.APPLY_FAILED.value

    def test_job_succeeded_is_ready_without_a_report(self):
        seam = _Seam()
        seam.jobs["b1"] = _job(TaskStatus.SUCCEEDED)
        repo = _Repo(bot={"status": "ACTIVE"})
        assert _mk(seam=seam, repo=repo).bot_status("b1") == BotState.READY.value

    def test_bot_up_job_gave_up_is_apply_failed_not_create_failed(self):
        # the anti-duplicate rule: a working bot is never reported as a
        # creation failure — the caller would spawn a second, billable copy
        seam = _Seam()
        seam.jobs["b1"] = _job(TaskStatus.FAILED, "post-container kept failing")
        repo = _Repo(bot={"status": "ACTIVE"})
        assert _mk(seam=seam, repo=repo).bot_status("b1") == BotState.APPLY_FAILED.value

    def test_terminal_job_bot_in_middle_state_is_terminal_not_provisioning(self):
        # PENDING/STOPPED are neither up nor dead; with the job terminal
        # nothing can drive them to READY, so PROVISIONING would only burn
        # the poller's whole 300s deadline into a generic timeout.
        for job_status in (TaskStatus.FAILED, TaskStatus.TIMED_OUT):
            for state in ("PENDING", "STOPPED"):
                seam = _Seam()
                seam.jobs["b1"] = _job(job_status, "gave up")
                repo = _Repo(bot={"status": state})
                assert _mk(seam=seam, repo=repo).bot_status(
                    "b1") == BotState.CREATE_FAILED.value, (job_status, state)


# ── delete ────────────────────────────────────────────────────────────────


class TestDelete:

    def test_pending_creation_is_discarded_not_soft_deleted(self):
        seam = _Seam()
        bot_svc = _BotSvc()
        platform = _mk(seam=seam, bot_svc=bot_svc)
        assert platform.delete_bot("b1") is True
        assert seam.discards == [("b1", "owner-1")]
        assert bot_svc.deleted == []

    def test_created_bot_is_soft_deleted(self):
        repo = _Repo(bot={"status": "ACTIVE"})
        bot_svc = _BotSvc()
        platform = _mk(repo=repo, bot_svc=bot_svc)
        assert platform.delete_bot("b1") is True
        assert bot_svc.deleted == [("b1", "owner-1")]

    def test_repository_failure_is_swallowed_to_false(self):
        class _Boom:
            def get_by_id_and_entity(self, bot_id, entity_id):
                raise RuntimeError("db down")

        platform = _mk(repo=_Boom())
        assert platform.delete_bot("b1") is False


# ── chat ─────────────────────────────────────────────────────────────────


class TestChat:

    def test_missing_runtime_binding_fails_clear(self):
        # A bot row without a runtime binding (no device_id) answers with
        # the precise refusal before any transport is attempted.
        platform = _mk(repo=_Repo(bot={"status": "ACTIVE"}))
        with pytest.raises(RuntimeError, match="no runtime binding"):
            platform.chat("b1", "objective='x'")

    def test_round_trip_returns_the_reply_text(self, monkeypatch):
        ws = _ScriptedEngineWs(reply="the answer is 42")
        platform = _mk()
        _patch_engine_routes(monkeypatch, ws)
        assert platform.chat("b1", "objective='y'") == "the answer is 42"
        (frame,) = ws.sent_frames
        assert frame["method"] == "chat.send"
        assert frame["params"]["message"] == "objective='y'"
        assert frame["params"]["x-iam-token"] == host_module._ZERO_CHECK_OPEN_API_TOKEN
        assert ws.closed

    def test_default_caller_keeps_the_fixed_session_key(self, monkeypatch):
        # A chat without a derived session keeps the historical address:
        # outside/default callers are unaffected by D-I.
        ws = _ScriptedEngineWs()
        platform = _mk()
        _patch_engine_routes(monkeypatch, ws)
        platform.chat("b1", "objective='x'")
        (frame,) = ws.sent_frames
        assert frame["params"]["sessionKey"] == "agent:main:default"

    def test_session_key_is_used_verbatim_when_provided(self, monkeypatch):
        ws = _ScriptedEngineWs()
        platform = _mk()
        _patch_engine_routes(monkeypatch, ws)
        platform.chat("b1", "objective='x'",
                      session_key="agent:agent-9:attempt:4")
        (frame,) = ws.sent_frames
        assert frame["params"]["sessionKey"] == "agent:agent-9:attempt:4"

    def test_cancelled_event_unwinds_within_the_receive_slice(
            self, monkeypatch):
        ws = _ScriptedEngineWs(reply="late", stall_seconds=30.0)
        platform = _mk()
        _patch_engine_routes(monkeypatch, ws)
        cancel = threading.Event()

        def _cancel_soon():
            time.sleep(0.05)
            cancel.set()

        setter = threading.Thread(target=_cancel_soon)
        setter.start()
        started = time.monotonic()
        reply = platform.chat("b1", "objective='x'", cancel_event=cancel)
        elapsed = time.monotonic() - started
        setter.join(timeout=1)
        assert reply == ""
        assert ws.closed
        # unwound at the next slice checkpoint, not after the 30s stall
        assert elapsed < 2.0

    def test_stalled_engine_leg_raises_within_the_bound(
            self, monkeypatch):
        # A hung engine WebSocket must free the call with the typed error
        # at the (patched-to-tiny) bound, not after the stall.
        ws = _ScriptedEngineWs(reply="never", stall_seconds=30.0)
        monkeypatch.setattr(host_module, "_CHAT_TIMEOUT_SECONDS", 0.05)
        monkeypatch.setattr(host_module, "_RECV_SLICE_SECONDS", 0.05)
        platform = _mk()
        platform._chat_timeout_seconds = host_module._CHAT_TIMEOUT_SECONDS
        _patch_engine_routes(monkeypatch, ws)
        started = time.monotonic()
        with pytest.raises(host_module.ChatTimeoutError,
                           match="did not finish within"):
            platform.chat("b1", "objective='x'")
        # freed at the bound, long before the 30s stall elapses
        assert time.monotonic() - started < 2.0

    def test_chat_timeout_is_a_runtime_error(self):
        # The subagent treats every chat failure through the common
        # Exception path; the typed error must stay in that family so a
        # timed-out chat reads as an attempt failure, not a wedge.
        assert issubclass(host_module.ChatTimeoutError, RuntimeError)