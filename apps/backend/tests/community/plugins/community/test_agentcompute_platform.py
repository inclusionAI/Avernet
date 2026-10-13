"""AvernetBotPlatform unit tests — the host adapter's contract, branch by branch.

Hand fakes for every dependency (no MagicMock, per house style); the create
leg records what the with-manifest submit would have received (patched at
``create_flow`` — the adapter imports it call-local, so the module-level
attribute patch is exactly what the call sees). The status table is pinned
branch for branch against the narrow mapping's documented vocabulary; the
chat leg exercises the real thread-bridge (async fakes under ``asyncio.run``
in the adapter's own thread).
"""
from __future__ import annotations

import types

import pytest

from agentclaw.community.core.agentcompute.domain.models import (
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
from agentclaw.community.plugins.community.agentcompute_platform import (
    AvernetBotPlatform,
)
from agentclaw.community.plugins.community.agentcompute_platform import (
    AgentComputePlatformConfig,
)

CREATE_FLOW_SUBMIT = (
    "agentclaw.community.core.bot_management.create_flow"
    ".submit_bot_creation_with_manifest"
)


def _config():
    # The config is now a single default-engine knob — engine/bot_type/
    # identity_file are per-agent spec fields, and owner comes from the
    # caller (the submitter), not from a static config.
    return AgentComputePlatformConfig(default_engine="openclaw")


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


class _GrtChat:
    def __init__(self, sandbox="sb-1", reply="final answer"):
        self.sandbox = sandbox
        self.reply = reply
        self.sent: list = []

    def get_sandbox_id(self, bot_id, owner_id):
        return self.sandbox

    async def connect_engine_ws(self, sandbox_id):
        return types.SimpleNamespace(
            close=_noop_async, closed=False,
        )

    async def chat_send(self, ws, message):
        self.sent.append(message)
        return types.SimpleNamespace(text=self.reply)


async def _noop_async():
    return None


def _job(status, last_error=None):
    return types.SimpleNamespace(status=status, last_error=last_error)


def _mk(config=None, seam=None, apply_=None, repo=None, bot_svc=None, grt=None):
    return AvernetBotPlatform(
        config=config or _config(),
        owner_id="owner-1",
        manifest_seam=seam or _Seam(),
        bot_service=bot_svc or _BotSvc(),
        passport_plugin=types.SimpleNamespace(),
        skill_set_factory=types.SimpleNamespace(),
        bot_repo=repo or _Repo(),
        apply_service=apply_ or _Apply(),
        space_context=_Space(),
        grt_chat=grt or _GrtChat(),
    )


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
        platform = _mk(grt=_GrtChat(sandbox=None))
        with pytest.raises(RuntimeError, match="no runtime binding"):
            platform.chat("b1", "objective='x'")

    def test_round_trip_returns_the_reply_text(self):
        grt = _GrtChat(reply="the answer is 42")
        platform = _mk(grt=grt)
        assert platform.chat("b1", "objective='y'") == "the answer is 42"
        assert grt.sent == ["objective='y'"]