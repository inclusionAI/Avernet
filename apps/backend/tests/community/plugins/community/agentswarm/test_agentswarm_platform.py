"""AvernetBotPlatform unit tests — the host adapter's contract, branch by branch.

Hand fakes for every dependency (no MagicMock, per house style); the create
leg records what the with-manifest submit would have received (patched at
``create_flow`` — the adapter imports it call-local, so the module-level
attribute patch is exactly what the call sees). The status table is pinned
branch for branch against the narrow mapping's documented vocabulary; the
chat leg exercises the real frame protocol over a scripted engine websocket
(``_patch_engine_routes`` swaps only the two container-provided edges —
endpoint resolution and the ws handshake): the fail-soft sessions.reset
prelude on the very key the upcoming chat.send addresses (the engine's
blessed fresh-conversation mechanism), the default/verbatim sessionKey
addressing, the fail-fast refusal answer (ok false / accepted false) that
frees a leg immediately instead of eating the receive deadline, and the
cooperative-cancel receive slices.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import types

import pytest

import agentclaw.community.plugins.community.agentswarm.host as host_module
import agentclaw.community.plugins.community.agentswarm.host_chat as host_chat_module
from agentclaw.community.plugins.community.agentswarm.host import _idem_digest
from agentclaw.community.core.agentswarm.domain.models import (
    BotCreateSpec, BotState,
)
from agentclaw.community.core.apply_engine.outcomes import (
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
    def __init__(self, bot=None, soft_deleted=False):
        self.bot = bot
        self.soft_deleted = soft_deleted

    def get_by_id_and_entity(self, bot_id, entity_id):
        return self.bot

    def is_soft_deleted(self, bot_id, entity_id):
        return self.soft_deleted


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
    """Fake engine websocket speaking the adapter's frame protocol between
    the (patched-away) handshake and close. Every frame the platform sends
    is recorded, and each request is answered scriptedly one frame at a
    time, mirroring the live engine's observed answers:

    - ``sessions.reset`` gets its res (ok, or the refusal when scripted);
    - ``chat.send`` gets the ok/accepted ack res and then the final chat
      event — or, when scripted, only the refused res (ok false, accepted
      false, no events following: exactly the live refusal trace).

    ``recv`` optionally stalls first, faking a hung engine; it also hangs
    when a scripted request produced no answer at all (the reset that the
    engine never answers)."""

    def __init__(self, reply="final answer", stall_seconds=0.0,
                 refuse_chat=False, refuse_reset=False,
                 reset_goes_silent=False):
        self.reply = reply
        self.stall_seconds = stall_seconds
        self.refuse_chat = refuse_chat
        self.refuse_reset = refuse_reset
        self.reset_goes_silent = reset_goes_silent
        self.sent_frames: list[dict] = []
        self.closed = False
        self._answers: list[dict] = []

    def _on_frame(self, frame: dict) -> None:
        method = frame.get("method")
        request_id = frame.get("id")
        if method == "sessions.reset":
            if self.reset_goes_silent:
                return
            ok = not self.refuse_reset
            self._answers.append({
                "type": "res", "id": request_id, "ok": ok,
                "payload": {"ok": ok, "key": frame["params"]["key"]},
            })
        elif method == "chat.send":
            if self.refuse_chat:
                self._answers.append({
                    "type": "res", "id": request_id, "ok": False,
                    "payload": {"accepted": False, "cached": None},
                })
                return
            self._answers.append({
                "type": "res", "id": request_id, "ok": True,
                "payload": {"accepted": True},
            })
            self._answers.append({
                "type": "event", "event": "chat",
                "payload": {
                    "state": "final",
                    "message": {"content": [
                        {"type": "text", "text": self.reply},
                    ]},
                    "id": request_id,
                },
            })

    async def send(self, raw):
        frame = json.loads(raw)
        self.sent_frames.append(frame)
        self._on_frame(frame)

    async def recv(self):
        if self.stall_seconds:
            await asyncio.sleep(self.stall_seconds)
        if not self._answers:
            # nothing scripted to answer with: hang like a stuck engine
            await asyncio.sleep(30.0)
        return json.dumps(self._answers.pop(0))

    async def close(self):
        self.closed = True


def _job(status, last_error=None):
    return types.SimpleNamespace(status=status, last_error=last_error)


def _mk(config=None, seam=None, apply_=None, repo=None, bot_svc=None,
        bridge_workers=None, baas=None):
    return AvernetBotPlatform(
        manifest_seam=seam or _Seam(),
        bot_service=bot_svc or _BotSvc(),
        passport_plugin=types.SimpleNamespace(),
        skill_set_factory=types.SimpleNamespace(),
        bot_repo=repo or _Repo(),
        apply_service=apply_ or _Apply(),
        space_context=_Space(),
        baas_service=baas or _Baas(),
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
        first = platform.create_bot(spec=spec, idempotency_key="run-1:scout",
                      owner_id="owner-1")
        again = platform.create_bot(spec=spec, idempotency_key="run-1:scout",
                      owner_id="owner-1")
        assert first == again and first.startswith("agent_swarm_") and len(first) == 28
        assert [w["bot_id"] for w in seen] == [first, first]

    def test_persona_name_is_id_format_desc_carries_the_label(self, monkeypatch):
        # The platform caps bot_name at 32 chars and a spec-derived name
        # (composite spawn name + digest) can exceed it, so the NAME is the
        # id-format digest and the descriptive label rides bot_desc.
        seen = self._record_submit(monkeypatch)
        platform = _mk()
        spec = BotCreateSpec(name="tester_rust_f9ed5997468d",
                             description="writes rust tests")
        bot_id = platform.create_bot(spec=spec, idempotency_key="run-9:a1",
                                     owner_id="owner-1")
        submitted = seen[0]
        assert submitted["bot_id"] == bot_id == submitted["spec"].bot_name
        assert len(bot_id) == 28
        assert not submitted["spec"].bot_name.startswith("tester_rust")
        desc = submitted["spec"].bot_desc
        assert desc.startswith("tester_rust_f9ed5997468d-")
        assert "writes rust tests" in desc

    def test_persona_desc_label_only_without_role(self, monkeypatch):
        seen = self._record_submit(monkeypatch)
        platform = _mk()
        spec = BotCreateSpec(name="scout")  # no description
        platform.create_bot(spec=spec, idempotency_key="run-9:a2",
                            owner_id="owner-1")
        desc = seen[0]["spec"].bot_desc
        # f"{spec.name}-{digest8}" and nothing else
        assert desc.startswith("scout-") and len(desc) == len("scout-") + 8

    def test_persona_desc_clamped_to_column_size(self, monkeypatch):
        seen = self._record_submit(monkeypatch)
        platform = _mk()
        spec = BotCreateSpec(name="scout", description="x" * 20000)
        platform.create_bot(spec=spec, idempotency_key="run-9:a3",
                            owner_id="owner-1")
        assert len(seen[0]["spec"].bot_desc) <= 1024

    def test_name_refusal_adopts_the_live_persona_row(self, monkeypatch):
        # D16 respawn after a mid-creation crash: bot_service's name check
        # refuses the re-create; the live row under our derived id is ours.
        from agentclaw.community.core.bot_management.services.bot_service import (
            BotNameExistsError,
        )

        def refuse(**kwargs):
            raise BotNameExistsError(
                f" Bot name '{kwargs['spec'].bot_name}' already exists")

        monkeypatch.setattr(create_flow, "submit_bot_creation_with_manifest",
                             refuse)
        platform = _mk(repo=_Repo(bot={"bot_id": "ac_adopted"}))
        spec = BotCreateSpec(name="scout", description="a surveyor")
        adopted = platform.create_bot(spec=spec, idempotency_key="run-1:scout",
                      owner_id="owner-1")
        assert adopted.startswith("agent_swarm_") and len(adopted) == 28

    def test_adopt_leg_unchanged_under_a_per_attempt_key(self, monkeypatch):
        # D-J only changes the CALLER-side key string; the create/adopt code
        # digests whatever key arrives. A per-attempt key replays the same
        # adopt resolution: refusal, live row under the SAME derived id,
        # one persona — never a duplicate create.
        from agentclaw.community.core.bot_management.services.bot_service import (
            BotNameExistsError,
        )

        derived = f"agent_swarm_{_idem_digest('run-1:agent-9:2', 16)}"

        def refuse(**kwargs):
            raise BotNameExistsError(
                f" Bot name '{kwargs['spec'].bot_name}' already exists")

        monkeypatch.setattr(create_flow, "submit_bot_creation_with_manifest",
                             refuse)
        platform = _mk(repo=_Repo(bot={"bot_id": derived}))
        spec = BotCreateSpec(name="scout", description="a surveyor")
        first = platform.create_bot(spec=spec,
                                    idempotency_key="run-1:agent-9:2",
                                    owner_id="owner-1")
        again = platform.create_bot(spec=spec,
                                    idempotency_key="run-1:agent-9:2",
                                    owner_id="owner-1")
        assert first == again == derived

    def test_name_refusal_without_our_row_reraises(self, monkeypatch):
        # Same-name row that is NOT our derived identity cannot exist
        # (digest names collide only across identical keys); a missing
        # row therefore re-raises instead of inventing a persona.
        from agentclaw.community.core.bot_management.services.bot_service import (
            BotNameExistsError,
        )

        def refuse(**kwargs):
            raise BotNameExistsError(
                f"Bot name '{kwargs['spec'].bot_name}' already exists")

        monkeypatch.setattr(create_flow, "submit_bot_creation_with_manifest",
                             refuse)
        platform = _mk()
        with pytest.raises(BotNameExistsError):
            platform.create_bot(
                spec=BotCreateSpec(name="scout", description="a surveyor"),
                idempotency_key="run-1:scout",
                owner_id="owner-1",
            )

    def test_submit_receives_the_persona_not_inventions(self, monkeypatch):
        seen = self._record_submit(monkeypatch)
        platform = _mk()
        spec = BotCreateSpec(
            name="clerk", description="a filing clerk",
            instructions="File weekly reports.",
        )
        platform.create_bot(spec=spec, idempotency_key="run-2:clerk",
                      owner_id="owner-1")
        (w,) = seen
        assert w["user_id"] == "owner-1" and w["modifier"] == "owner-1"
        document = w["document"]
        assert "schema_version: 1" in document
        assert "type: SOUL.md" in document
        assert "File weekly reports." in document
        host_spec = w["spec"]
        # Id-format naming contract: bot_name IS the derived id (the
        # platform's 32-char bot_name cap forbids spec-derived names);
        # the descriptive label moves into bot_desc ahead of the role.
        assert host_spec.bot_name == w["bot_id"]
        assert host_spec.bot_name.startswith("agent_swarm_")
        assert host_spec.bot_desc.startswith(
            f"clerk-{first8(w['bot_id'][12:])}")
        assert "a filing clerk" in host_spec.bot_desc
        assert host_spec.engine_type == "openclaw"
        assert w["context"].deployment_mode.value == "cloud"
        assert document.count("template_display") == 0

    def test_empty_instructions_stay_empty_and_visible(self, monkeypatch):
        seen = self._record_submit(monkeypatch)
        platform = _mk()
        spec = BotCreateSpec(name="blank")
        platform.create_bot(spec=spec, idempotency_key="run-3:blank",
                      owner_id="owner-1")
        document = seen[0]["document"]
        assert "type: SOUL.md" in document
        # empty content is the value ("no persona text"), not a filler default
        assert "content: ''" in document
        assert "Complete the assigned task" not in document

    def test_spec_manifest_is_submitted_verbatim(self, monkeypatch):
        # A spec carrying a full config_manifest document submits THAT
        # outer-whitespace-tight document byte-identical — never
        # re-synthesized, never merged with the identity-only persona
        # shape. The platform's own preflight (inside the submit) is the
        # single grammar authority.
        seen = self._record_submit(monkeypatch)
        platform = _mk()
        document = (
            "schema_version: 1\n"
            "manifest:\n"
            "  identity:\n"
            "    - type: SOUL.md\n"
            "      content: 'You are a meticulous greeter.'\n"
            "  mcp: []\n"
            "  resources: []\n"
            "  skills: []\n"
            "  cli_tools: []\n"
            "script:\n"
            "  body: 'echo manifest-provisioned'"
        )
        spec = BotCreateSpec(
            name="clerk", description="a filing clerk",
            instructions="File weekly reports.",
            config_manifest=document,
        )
        platform.create_bot(spec=spec, idempotency_key="run-4:clerk",
                      owner_id="owner-1")
        (w,) = seen
        assert w["document"] == document
        # instructions do NOT leak into a document the caller authored
        assert "File weekly reports." not in w["document"]

    def test_blank_manifest_keeps_the_synthesized_persona_document(
            self, monkeypatch):
        # Regression pin: a spec without a document composes exactly the
        # identity-only manifest synthesized today — identity type from
        # the spec's identity_file, content from its instructions.
        seen = self._record_submit(monkeypatch)
        platform = _mk()
        spec = BotCreateSpec(
            name="clerk", description="a filing clerk",
            instructions="File weekly reports.",
            identity_file="RULES.md",
        )
        platform.create_bot(spec=spec, idempotency_key="run-5:clerk",
                      owner_id="owner-1")
        (w,) = seen
        assert w["document"] == (
            "schema_version: 1\n"
            "manifest:\n"
            "  identity:\n"
            "  - type: RULES.md\n"
            "    content: File weekly reports.\n"
        )
        assert "mcp" not in w["document"] and "skills" not in w["document"]

    def test_whitespace_manifest_keeps_the_synthesized_persona_document(
            self, monkeypatch):
        # "Blank" reads as stripped-blank: a whitespace-only document is
        # not a manifest and must fall back to the synthesized persona
        # document, never be preflighted as a parse-failing YAML doc.
        seen = self._record_submit(monkeypatch)
        platform = _mk()
        spec = BotCreateSpec(
            name="clerk", instructions="File weekly reports.",
            config_manifest="   \n\t  \n",
        )
        platform.create_bot(spec=spec, idempotency_key="run-5b:clerk",
                      owner_id="owner-1")
        (w,) = seen
        assert w["document"] == (
            "schema_version: 1\n"
            "manifest:\n"
            "  identity:\n"
            "  - type: SOUL.md\n"
            "    content: File weekly reports.\n"
        )

    def test_manifest_validation_refusal_is_non_retryable(
            self, monkeypatch):
        # A preflight-refused document is deterministic: every attempt
        # would refuse identically, so the host maps the platform's
        # validation error to the swarm's non-retryable marker instead
        # of letting the pool burn the attempt budget.
        from agentclaw.community.core.bot_config_manifest.schema import (
            ManifestValidationError, Violation,
        )

        def refuse(**kwargs):
            raise ManifestValidationError([Violation(
                location="manifest.skills[0]",
                code="content_not_a_skill_package",
                message="skills entries must be fetchable packages",
            )])

        monkeypatch.setattr(create_flow, "submit_bot_creation_with_manifest",
                            refuse)
        platform = _mk()
        from agentclaw.community.core.agentswarm.domain.models import (
            NonRetryableProvisioningError,
        )
        with pytest.raises(NonRetryableProvisioningError,
                           match="must be fetchable packages"):
            platform.create_bot(
                spec=BotCreateSpec(
                    name="clerk",
                    config_manifest="schema_version: 1\nmanifest:\n"
                                    "  skills:\n"
                                    "    - name: x\n      content: y\n"),
                idempotency_key="run-6:clerk",
                owner_id="owner-1",
            )

    def test_different_idempotency_keys_get_different_identities(
            self, monkeypatch):
        seen = self._record_submit(monkeypatch)
        platform = _mk()
        spec = BotCreateSpec(name="scout")
        platform.create_bot(spec=spec, idempotency_key="run-a:scout",
                      owner_id="owner-1")
        platform.create_bot(spec=spec, idempotency_key="run-b:scout",
                      owner_id="owner-1")
        names = {w["spec"].bot_name for w in seen}
        ids = {w["bot_id"] for w in seen}
        assert len(names) == 2 and len(ids) == 2


def first8(hexstr):
    return hexstr[:8]


# ── status: the narrow table ─────────────────────────────────────────────


class TestStatusNarrowTable:

    def test_no_job_answer_is_provisioning(self):
        platform = _mk()
        assert platform.bot_status("ac_unknown", owner_id="owner-1") == BotState.PROVISIONING.value

    def test_live_job_windows_are_provisioning(self):
        for status in (TaskStatus.PENDING, TaskStatus.RUNNING):
            seam = _Seam()
            seam.jobs["b1"] = _job(status)
            assert _mk(seam=seam).bot_status("b1", owner_id="owner-1") == BotState.PROVISIONING.value

    def test_terminal_job_without_bot_maps_the_jobs_own_answer(self):
        cases = [
            (TaskStatus.TIMED_OUT, None, BotState.AUTHORIZATION_EXPIRED.value),
            (TaskStatus.FAILED, "the authorization window elapsed",
             BotState.AUTHORIZATION_EXPIRED.value),
            (TaskStatus.FAILED, "the bot could not be provisioned: quota",
             BotState.CREATE_FAILED.value),
            # The authorization's own terminal verdicts, verbatim from the
            # service — the only path to REJECTED/EXPIRED by status.
            (TaskStatus.FAILED, "authorization did not complete: REJECTED",
             BotState.AUTHORIZATION_REJECTED.value),
            (TaskStatus.FAILED, "authorization did not complete: EXPIRED",
             BotState.AUTHORIZATION_EXPIRED.value),
            # A service-side terminal status is NOT a human decline: the
            # PRE 04fadbbf incident died to a fabricated REJECTED here.
            (TaskStatus.FAILED, "authorization did not complete: APPLY_FAILED",
             BotState.CREATE_FAILED.value),
            # Any other terminal job error is a platform failure too —
            # never an authorization verdict nobody gave.
            (TaskStatus.FAILED, "declined by user",
             BotState.CREATE_FAILED.value),
        ]
        for status, err, want in cases:
            seam = _Seam()
            seam.jobs["b1"] = _job(status, err)
            assert _mk(seam=seam).bot_status("b1", owner_id="owner-1") == want, (status, err)

    def test_dead_bot_is_create_failed_even_with_live_job(self):
        seam = _Seam()
        seam.jobs["b1"] = _job(TaskStatus.SUCCEEDED)
        repo = _Repo(bot={"status": "FAILED"})
        assert _mk(seam=seam, repo=repo).bot_status("b1", owner_id="owner-1") == BotState.CREATE_FAILED.value

    def test_report_success_with_container_up_is_ready(self):
        seam = _Seam()
        seam.jobs["b1"] = _job(TaskStatus.RUNNING)
        repo = _Repo(bot={"status": "ACTIVE"})
        apply_ = _Apply(types.SimpleNamespace(
            trigger=CREATE_ON_CONTAINER_TRIGGER, status=ApplyStatus.SUCCEEDED))
        assert _mk(seam=seam, repo=repo, apply_=apply_).bot_status(
            "b1", owner_id="owner-1") == BotState.READY.value

    def test_report_success_with_container_coming_is_provisioning(self):
        # record-first engines apply before the container exists
        seam = _Seam()
        seam.jobs["b1"] = _job(TaskStatus.RUNNING)
        repo = _Repo(bot={"status": "PENDING"})
        apply_ = _Apply(types.SimpleNamespace(
            trigger=CREATE_ON_CONTAINER_TRIGGER, status=ApplyStatus.SUCCEEDED))
        assert _mk(seam=seam, repo=repo, apply_=apply_).bot_status(
            "b1", owner_id="owner-1") == BotState.PROVISIONING.value

    def test_failed_creation_report_is_apply_failed(self):
        seam = _Seam()
        seam.jobs["b1"] = _job(TaskStatus.RUNNING)
        repo = _Repo(bot={"status": "ACTIVE"})
        apply_ = _Apply(types.SimpleNamespace(
            trigger=CREATE_ON_CONTAINER_TRIGGER, status=ApplyStatus.FAILED))
        assert _mk(seam=seam, repo=repo, apply_=apply_).bot_status(
            "b1", owner_id="owner-1") == BotState.APPLY_FAILED.value

    def test_failed_report_mid_boot_with_live_device_is_provisioning(self):
        # the quota-race flap: apply FAILED while the durable publish
        # pipeline still owns the boot — a burst APPLY_FAILED panic
        # destroys a healthy mid-boot bot (re-create wedges upstream)
        seam = _Seam()
        seam.jobs["b1"] = _job(TaskStatus.RUNNING)
        repo = _Repo(bot={"status": "PENDING", "device_id": "BOT-x"})
        apply_ = _Apply(types.SimpleNamespace(
            trigger=CREATE_ON_CONTAINER_TRIGGER, status=ApplyStatus.FAILED))
        assert _mk(seam=seam, repo=repo, apply_=apply_).bot_status(
            "b1", owner_id="owner-1") == BotState.PROVISIONING.value

    def test_failed_report_mid_boot_without_device_is_apply_failed(self):
        # no pipeline left to wait on: the apply verdict stands
        seam = _Seam()
        seam.jobs["b1"] = _job(TaskStatus.RUNNING)
        repo = _Repo(bot={"status": "PENDING"})
        apply_ = _Apply(types.SimpleNamespace(
            trigger=CREATE_ON_CONTAINER_TRIGGER, status=ApplyStatus.FAILED))
        assert _mk(seam=seam, repo=repo, apply_=apply_).bot_status(
            "b1", owner_id="owner-1") == BotState.APPLY_FAILED.value

    def test_job_succeeded_is_ready_without_a_report(self):
        seam = _Seam()
        seam.jobs["b1"] = _job(TaskStatus.SUCCEEDED)
        repo = _Repo(bot={"status": "ACTIVE"})
        assert _mk(seam=seam, repo=repo).bot_status("b1", owner_id="owner-1") == BotState.READY.value

    def test_bot_up_job_gave_up_is_apply_failed_not_create_failed(self):
        # the anti-duplicate rule: a working bot is never reported as a
        # creation failure — the caller would spawn a second, billable copy
        seam = _Seam()
        seam.jobs["b1"] = _job(TaskStatus.FAILED, "post-container kept failing")
        repo = _Repo(bot={"status": "ACTIVE"})
        assert _mk(seam=seam, repo=repo).bot_status("b1", owner_id="owner-1") == BotState.APPLY_FAILED.value

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
                    "b1", owner_id="owner-1") == BotState.CREATE_FAILED.value, (job_status, state)


# ── delete ────────────────────────────────────────────────────────────────


class TestDelete:

    def test_pending_creation_is_discarded_not_soft_deleted(self):
        seam = _Seam()
        seam.jobs["b1"] = object()
        bot_svc = _BotSvc()
        platform = _mk(seam=seam, bot_svc=bot_svc)
        assert platform.delete_bot("b1", owner_id="owner-1") is True
        assert seam.discards == [("b1", "owner-1")]
        assert bot_svc.deleted == []

    def test_never_submitted_creation_has_nothing_to_discard(self):
        # No bot and no creation job: submission never landed a row, so the
        # pre-container phase never wrote the managed-files / CLI-tools
        # prefixes. Discarding anyway lists a prefix that cannot exist, on
        # every teardown of a slice whose creation never got that far.
        seam = _Seam()
        bot_svc = _BotSvc()
        platform = _mk(seam=seam, bot_svc=bot_svc)
        assert platform.delete_bot("b1", owner_id="owner-1") is True
        assert seam.discards == []
        assert bot_svc.deleted == []

    def test_unreadable_job_still_discards(self):
        # An unknown job state is not proof that nothing was written; only an
        # absent row is. A failing read must fall back to the cleanup.
        class _UnreadableSeam(_Seam):
            def find_job(self, *, entity_id, bot_id):
                raise RuntimeError("job store down")

        seam = _UnreadableSeam()
        platform = _mk(seam=seam, bot_svc=_BotSvc())
        assert platform.delete_bot("b1", owner_id="owner-1") is True
        assert seam.discards == [("b1", "owner-1")]

    def test_created_bot_is_soft_deleted(self):
        repo = _Repo(bot={"status": "ACTIVE"})
        bot_svc = _BotSvc()
        platform = _mk(repo=repo, bot_svc=bot_svc)
        assert platform.delete_bot("b1", owner_id="owner-1") is True
        assert bot_svc.deleted == [("b1", "owner-1")]

    def test_already_soft_deleted_bot_is_not_discarded_again(self):
        # The live lookups filter is_delete=0, so an already-deleted bot reads
        # like a pending one. Re-discarding it repeats the managed-files purge
        # on every teardown; a deleted bot has nothing left to undo.
        seam = _Seam()
        bot_svc = _BotSvc()
        repo = _Repo(bot=None, soft_deleted=True)
        platform = _mk(seam=seam, bot_svc=bot_svc, repo=repo)
        assert platform.delete_bot("b1", owner_id="owner-1") is True
        assert seam.discards == []
        assert bot_svc.deleted == []

    def test_repository_failure_is_swallowed_to_false(self):
        class _Boom:
            def get_by_id_and_entity(self, bot_id, entity_id):
                raise RuntimeError("db down")

        platform = _mk(repo=_Boom())
        assert platform.delete_bot("b1", owner_id="owner-1") is False


# ── chat ─────────────────────────────────────────────────────────────────


class TestChat:

    def test_missing_runtime_binding_fails_clear(self):
        # A bot row without a runtime binding (no device_id) answers with
        # the precise refusal before any transport is attempted.
        platform = _mk(repo=_Repo(bot={"status": "ACTIVE"}))
        with pytest.raises(RuntimeError, match="no runtime binding"):
            platform.chat("b1", "objective='x'",
                      owner_id="owner-1")

    def test_round_trip_returns_the_reply_text(self, monkeypatch):
        ws = _ScriptedEngineWs(reply="the answer is 42")
        platform = _mk()
        _patch_engine_routes(monkeypatch, ws)
        assert platform.chat("b1", "objective='y'",
                      owner_id="owner-1") == "the answer is 42"
        assert [f["method"] for f in ws.sent_frames] == [
            "sessions.reset", "chat.send",
        ]
        chat_frame = ws.sent_frames[-1]
        assert chat_frame["params"]["message"] == "objective='y'"
        assert chat_frame["params"]["x-iam-token"] == host_module._ZERO_CHECK_OPEN_API_TOKEN
        assert ws.closed

    def test_sessions_reset_precedes_chat_send_on_the_default_key(
            self, monkeypatch):
        # A chat without an explicit key resets and then addresses the
        # engine-served default session: the reset is the engine-blessed
        # fresh-conversation mechanism, and it must run on the very key
        # the upcoming chat.send uses, before that frame goes out.
        ws = _ScriptedEngineWs()
        platform = _mk()
        _patch_engine_routes(monkeypatch, ws)
        platform.chat("b1", "objective='x'",
                      owner_id="owner-1")
        assert [f["method"] for f in ws.sent_frames] == [
            "sessions.reset", "chat.send",
        ]
        reset_frame, chat_frame = ws.sent_frames
        assert reset_frame["params"]["key"] == "agent:main:default"
        assert chat_frame["params"]["sessionKey"] == "agent:main:default"
        assert reset_frame["id"] != chat_frame["id"]

    def test_session_key_is_used_verbatim_when_provided(self, monkeypatch):
        # The widened SPI parameter survives: an explicit caller with a
        # known-good key gets both the reset and the send on that key.
        ws = _ScriptedEngineWs()
        platform = _mk()
        _patch_engine_routes(monkeypatch, ws)
        platform.chat("b1", "objective='x'",
                      session_key="agent:agent-9:attempt:4",
                      owner_id="owner-1")
        reset_frame, chat_frame = ws.sent_frames
        assert reset_frame["params"]["key"] == "agent:agent-9:attempt:4"
        assert chat_frame["params"]["sessionKey"] == "agent:agent-9:attempt:4"

    def test_unanswered_session_reset_is_fail_soft_and_logged(
            self, monkeypatch, caplog):
        # A reset that never gets its res only warns (bounded by the
        # reset wait) and the chat proceeds: context freshness is an
        # answer-quality risk, never a liveness one.
        ws = _ScriptedEngineWs(reset_goes_silent=True)
        monkeypatch.setattr(host_chat_module, "_SESSION_RESET_SECONDS", 0.1)
        platform = _mk()
        _patch_engine_routes(monkeypatch, ws)
        with caplog.at_level(
                "WARNING",
                logger="agentclaw.community.plugins.community.agentswarm.host"):
            assert platform.chat("b1", "objective='x'",
                      owner_id="owner-1") == "final answer"
        assert "session reset failed; chat proceeds" in caplog.text
        assert "session=agent:main:default" in caplog.text
        chat_frame = [f for f in ws.sent_frames
                      if f.get("method") == "chat.send"]
        assert len(chat_frame) == 1

    def test_refused_session_reset_is_fail_soft_and_logged(
            self, monkeypatch, caplog):
        # A reset answered ok=false is an error, not a refusal of the
        # chat: warn and continue to the send.
        ws = _ScriptedEngineWs(refuse_reset=True)
        platform = _mk()
        _patch_engine_routes(monkeypatch, ws)
        with caplog.at_level(
                "WARNING",
                logger="agentclaw.community.plugins.community.agentswarm.host"):
            assert platform.chat("b1", "objective='x'",
                      owner_id="owner-1") == "final answer"
        assert "session reset failed; chat proceeds" in caplog.text

    def test_refused_chat_send_raises_promptly(self, monkeypatch):
        # The live refusal trace: the engine answers the outstanding
        # chat.send with ok=false/accepted=false and then never events —
        # the leg must raise right away with the session key and the
        # abbreviated payload, not sit out the receive deadline.
        ws = _ScriptedEngineWs(refuse_chat=True)
        platform = _mk()
        _patch_engine_routes(monkeypatch, ws)
        started = time.monotonic()
        with pytest.raises(RuntimeError, match="refused chat.send") as caught:
            platform.chat("b1", "objective='x'",
                      owner_id="owner-1")
        elapsed = time.monotonic() - started
        assert elapsed < host_module._CHAT_TIMEOUT_SECONDS / 10
        message = str(caught.value)
        assert "session agent:main:default" in message
        assert '"accepted": false' in message
        assert ws.sent_frames[-1]["method"] == "chat.send"

    def test_non_matching_res_frames_do_not_fail_the_chat(
            self, monkeypatch):
        # Only the res that carries the outstanding request_id speaks for
        # this chat: unrelated res frames keep flowing past the loop.
        ws = _ScriptedEngineWs()
        original_on_frame = ws._on_frame

        def _on_frame_with_noise(frame):
            original_on_frame(frame)
            if frame.get("method") == "sessions.reset":
                ws._answers.insert(0, {
                    "type": "res", "id": "someone-elses-request",
                    "ok": False, "payload": {"accepted": False},
                })

        ws._on_frame = _on_frame_with_noise
        platform = _mk()
        _patch_engine_routes(monkeypatch, ws)
        assert platform.chat("b1", "objective='x'",
                      owner_id="owner-1") == "final answer"

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
        reply = platform.chat("b1", "objective='x'", cancel_event=cancel,
                               owner_id="owner-1")
        elapsed = time.monotonic() - started
        setter.join(timeout=1)
        assert reply == ""
        assert ws.closed
        # unwound at the next slice checkpoint, not after the 30s stall
        assert elapsed < 2.0

    def test_stalled_engine_leg_raises_within_the_bound(
            self, monkeypatch):
        # A hung engine WebSocket must free the call with the typed error
        # at the (patched-to-tiny) bound, not after the stall — the
        # reset prelude included: its own tiny bound abdicates first,
        # fail-soft, and the deadline below still bounds the chat.
        ws = _ScriptedEngineWs(reply="never", stall_seconds=30.0)
        monkeypatch.setattr(host_chat_module, "_CHAT_TIMEOUT_SECONDS", 0.05)
        monkeypatch.setattr(host_chat_module, "_RECV_SLICE_SECONDS", 0.05)
        monkeypatch.setattr(host_chat_module, "_SESSION_RESET_SECONDS", 0.05)
        platform = _mk()
        platform._chat_timeout_seconds = host_chat_module._CHAT_TIMEOUT_SECONDS
        _patch_engine_routes(monkeypatch, ws)
        started = time.monotonic()
        with pytest.raises(host_module.ChatTimeoutError,
                           match="did not finish within"):
            platform.chat("b1", "objective='x'",
                      owner_id="owner-1")
        # freed at the bound, long before the 30s stall elapses
        assert time.monotonic() - started < 2.0

    def test_chat_timeout_is_a_runtime_error(self):
        # The subagent treats every chat failure through the common
        # Exception path; the typed error must stay in that family so a
        # timed-out chat reads as an attempt failure, not a wedge.
        assert issubclass(host_module.ChatTimeoutError, RuntimeError)


# ── identity law: blank per-call owner is refused ─────────────────────────


class TestBlankOwnerRefusal:

    def test_every_method_refuses_a_blank_owner_immediately(self):
        platform = _mk()
        spec = BotCreateSpec(name="scout")
        for blank in ("", "   "):
            with pytest.raises(RuntimeError, match="no ambient identity"):
                platform.create_bot(spec=spec,
                                     idempotency_key="run-1:scout",
                                     owner_id=blank)
            with pytest.raises(RuntimeError, match="no ambient identity"):
                platform.bot_status("b1", owner_id=blank)
            with pytest.raises(RuntimeError, match="no ambient identity"):
                platform.delete_bot("b1", owner_id=blank)
            with pytest.raises(RuntimeError, match="no ambient identity"):
                platform.chat("b1", "objective='x'", owner_id=blank)

    def test_blank_owner_create_never_reaches_the_submit_chain(
            self, monkeypatch,
    ):
        seen: list = []

        def fake_submit(**kwargs):
            seen.append(kwargs)

        monkeypatch.setattr(create_flow, "submit_bot_creation_with_manifest",
                            fake_submit)
        platform = _mk()
        with pytest.raises(RuntimeError, match="no ambient identity"):
            platform.create_bot(spec=BotCreateSpec(name="scout"),
                                idempotency_key="run-1:scout")
        assert seen == []

# ── the engine-chat transport's own seams (host_chat.py) ────────────────

class TestChatTimeoutProperty:

    def test_property_exposes_the_deployment_default(self):
        platform = _mk()
        assert platform.chat_timeout_seconds == 1800.0


class _BaasInfo(types.SimpleNamespace):
    """ws-info stand-in with configurable url/token."""

    def get_ws_info_by_bot_uuid(self, *, bot_uuid, port, path):
        return self


class _BaasRaiser:
    def get_ws_info_by_bot_uuid(self, *, bot_uuid, port, path):
        raise ConnectionError("baas down")


class TestEngineEndpoint:

    def test_missing_bot_row_is_not_found_for_chat(self):
        platform = _mk(repo=_Repo(bot=None))
        with pytest.raises(RuntimeError, match="not found for chat"):
            platform._engine_endpoint("b1", "owner-1")

    def test_bot_without_runtime_binding_refuses(self):
        platform = _mk(repo=_Repo(bot={"status": "ACTIVE"}))
        with pytest.raises(RuntimeError, match="no runtime binding"):
            platform._engine_endpoint("b1", "owner-1")

    def test_baas_ws_info_failure_is_a_binding_refusal(self):
        platform = _mk(repo=_Repo(bot={"device_id": "dev-1"}),
                       baas=_BaasRaiser())
        with pytest.raises(RuntimeError, match="no runtime binding"):
            platform._engine_endpoint("b1", "owner-1")

    def test_empty_ws_pair_is_a_binding_refusal(self):
        platform = _mk(
            repo=_Repo(bot={"device_id": "dev-1"}),
            baas=_BaasInfo(ws_url="  ", token=""),
        )
        with pytest.raises(RuntimeError, match="no runtime binding"):
            platform._engine_endpoint("b1", "owner-1")

    def test_resolved_pair_returns_the_endpoint(self):
        platform = _mk(
            repo=_Repo(bot={"device_id": "dev-1"}),
            baas=_BaasInfo(ws_url="wss://engine/x", token="tok"),
        )
        assert platform._engine_endpoint("b1", "owner-1") \
            == ("wss://engine/x", "tok")


class _HandshakeWs:
    """Answers the `connect` handshake per the scripted mode."""

    def __init__(self, *, handshake_ok=True, recv_error=None):
        self.handshake_ok = handshake_ok
        self.recv_error = recv_error
        self.closed = False

    async def send(self, raw):
        pass

    async def recv(self):
        if self.recv_error is not None:
            raise self.recv_error
        return json.dumps({
            "type": "res", "ok": self.handshake_ok,
            "payload": {"scopes": []},
        })

    async def close(self):
        self.closed = True


class TestConnectHandshake:

    def _connect_once(self, monkeypatch, ws, url="ws://engine/x"):
        seen: dict = {}

        async def fake_connect(u, **kwargs):
            seen["url"], seen["kwargs"] = u, kwargs
            return ws

        monkeypatch.setattr("websockets.connect", fake_connect)
        fut = asyncio.run(
            AvernetBotPlatform._connect_engine_once(None, url, "tok"))
        return fut, seen

    def test_handshake_ok_returns_the_socket(self, monkeypatch):
        ws = _HandshakeWs(handshake_ok=True)
        out, _ = self._connect_once(monkeypatch, ws)
        assert out is ws

    def test_wss_url_builds_an_unverified_ssl_context(self, monkeypatch):
        import ssl as _ssl
        ws = _HandshakeWs(handshake_ok=True)
        out, seen = self._connect_once(
            monkeypatch, ws, url="wss://engine/x")
        assert out is ws
        assert isinstance(
            seen["kwargs"]["ssl"], _ssl.SSLContext)

    def test_refused_handshake_closes_and_raises(self, monkeypatch):
        ws = _HandshakeWs(handshake_ok=False)
        with pytest.raises(ConnectionError,
                           match="engine handshake failed"):
            self._connect_once(monkeypatch, ws)
        assert ws.closed

    def test_broken_handshake_closes_and_raises(self, monkeypatch):
        ws = _HandshakeWs(recv_error=ValueError("stream died"))
        with pytest.raises(ConnectionError,
                           match="engine handshake failed"):
            self._connect_once(monkeypatch, ws)
        assert ws.closed

    def test_connect_transport_failure_raises_connection_error(
            self, monkeypatch):
        async def fake_connect(u, **kwargs):
            raise OSError("dial refused")

        monkeypatch.setattr("websockets.connect", fake_connect)
        with pytest.raises(ConnectionError,
                           match="engine websocket connect failed"):
            asyncio.run(
                AvernetBotPlatform._connect_engine_once(
                    None, "ws://engine/x", "tok"))


class _CannedEngineWs:
    """Scripted chat frames for the receive loop: the reset res is
    answered automatically, chat.send gets the scripted list, and an
    empty script stalls into the (patched-short) deadline."""

    def __init__(self, chat_frames, *, ack_refuse=False):
        self.chat_frames = list(chat_frames)
        self.ack_refuse = ack_refuse
        self.pending: list = []
        self.sent_frames: list = []
        self.closed = False

    async def send(self, raw):
        frame = json.loads(raw)
        self.sent_frames.append(frame)
        if frame.get("method") == "sessions.reset":
            self.pending.append({
                "type": "res", "id": frame["id"], "ok": True,
                "payload": {"ok": True},
            })
        elif frame.get("method") == "chat.send":
            self.pending.append({
                "type": "res", "id": frame["id"],
                "ok": not self.ack_refuse,
                "payload": ({"accepted": False}
                            if self.ack_refuse else {"accepted": True}),
            })
            self.pending.extend(self.chat_frames)

    async def recv(self):
        if self.pending:
            item = self.pending.pop(0)
            if isinstance(item, Exception):
                raise item
            if isinstance(item, str):
                return item
            return json.dumps(item)
        await asyncio.sleep(30)

    async def close(self):
        self.closed = True


class TestChatSendFrames:

    def _chat(self, monkeypatch, ws, *, timeout=30.0):
        monkeypatch.setattr(host_chat_module, "_CHAT_TIMEOUT_SECONDS",
                            timeout)
        monkeypatch.setattr(
            AvernetBotPlatform, "_engine_endpoint",
            lambda self, bot_id, owner: ("ws://fake", "tok"))

        async def connect(self_, ws_url, token):
            return ws

        monkeypatch.setattr(AvernetBotPlatform, "_connect_engine", connect)

        async def reset(self_, ws, session_key, cancel_event):
            pass

        monkeypatch.setattr(AvernetBotPlatform, "_reset_session", reset)
        monkeypatch.setattr(AvernetBotPlatform, "_reset_session_round",
                            reset)
        platform = _mk()
        platform._chat_timeout_seconds = timeout
        return platform

    def test_stale_frames_are_ignored_until_the_final_event(
            self, monkeypatch):
        ws = _CannedEngineWs([
            "not json at all",
            {"type": "res", "id": "unrelated", "ok": True},
            {"type": "event", "event": "presence"},
            {"type": "event", "event": "chat", "payload": {
                "state": "final",
                "message": {"content": [{"type": "text", "text": "42"}]},
            }},
        ])
        platform = self._chat(monkeypatch, ws)
        assert platform.chat("b1", "objective='x'", owner_id="owner-1") == "42"

    def test_accepted_false_ack_is_the_engines_refusal(self, monkeypatch):
        ws = _CannedEngineWs([], ack_refuse=True)
        platform = self._chat(monkeypatch, ws)
        with pytest.raises(RuntimeError, match="engine refused"):
            platform.chat("b1", "x", owner_id="owner-1")

    def test_agent_error_event_fails_the_leg(self, monkeypatch):
        ws = _CannedEngineWs([
            {"type": "event", "event": "chat", "payload": {
                "state": "error", "errorMessage": "model on fire"},
            }])
        platform = self._chat(monkeypatch, ws)
        with pytest.raises(RuntimeError, match="model on fire"):
            platform.chat("b1", "x", owner_id="owner-1")

    def test_aborted_event_fails_the_leg(self, monkeypatch):
        ws = _CannedEngineWs([
            {"type": "event", "event": "chat", "payload": {
                "state": "aborted",
            }}])
        platform = self._chat(monkeypatch, ws)
        with pytest.raises(RuntimeError, match="aborted"):
            platform.chat("b1", "x", owner_id="owner-1")

    def test_dead_socket_fails_as_connection_error(self, monkeypatch):
        ws = _CannedEngineWs([ConnectionError("rcvd 1006 (closed)")])
        platform = self._chat(monkeypatch, ws)
        with pytest.raises(ConnectionError, match="ws closed"):
            platform.chat("b1", "x", owner_id="owner-1")

    def test_unanswered_chat_dies_at_the_deadline(self, monkeypatch):
        ws = _CannedEngineWs([])
        platform = self._chat(monkeypatch, ws, timeout=0.05)
        monkeypatch.setattr(host_chat_module, "_RECV_SLICE_SECONDS", 0.01)
        with pytest.raises(host_chat_module.ChatTimeoutError):
            platform.chat("b1", "x", owner_id="owner-1")
