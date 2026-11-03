"""bot_status's human gate — the awaiting-authorization mapping.

Pins the human-gate behavior in the
host adapter's narrow table: while a swarm creation's passport application is
actually PENDING inside its authorization window, ``bot_status`` answers
``AWAITING_AUTHORIZATION`` with the descriptor (state, iframe_url,
redirect_url, deadline) served from the durable job payload — never from a
new passport application; every other outcome keeps its pre-change answer
(REJECTED → ``AUTHORIZATION_REJECTED``; window-elapsed →
``AUTHORIZATION_EXPIRED``; issued → the normal provisioning path toward
READY, consent URLs in the payload notwithstanding), and every bot in a run
is gated independently.

Hand fakes per house style (no MagicMock): the local seam serves scripted
TaskRecord-like jobs carrying the same payload keys ``build_create_job_payload``
freezes, and the passport stand-in answers the same ``query_auth_status``
shape (``{"status": ...}``) the creation job's own authorization poll
consumes. Assertions express states and descriptor fields, not call order.
"""
from __future__ import annotations

import types
from datetime import datetime, timedelta

from agentclaw.community.core.agentswarm.domain.models import BotState
from agentclaw.community.core.bot_config_manifest.create_job import (
    AUTHORIZATION_WINDOW_ELAPSED,
)
from agentclaw.community.core.task_queue.types import TaskStatus
from agentclaw.community.plugins.community.agentswarm.host import (
    AvernetBotPlatform,
)

_WINDOW_SECONDS = 600


class _Seam:
    def __init__(self):
        self.jobs: dict = {}

    def find_job(self, *, entity_id, bot_id):
        return self.jobs.get(bot_id)


class _Apply:
    def __init__(self, report=None):
        self.report = report

    def last_apply(self, *, entity_id, bot_id):
        return self.report


class _Repo:
    def __init__(self, bot=None):
        self.bot = bot
        self.rows: dict = {}

    def get_by_id_and_entity(self, bot_id, entity_id):
        return self.rows.get(bot_id, self.bot)


class _BotSvc:
    def delete_bot(self, bot_id, user_id, nick_name=None):
        return True


class _Space:
    def resolve_current(self, owner_id, header_space_id=None):
        return types.SimpleNamespace(kind="personal", numeric_id=None)


class _Baas:
    def get_ws_info_by_bot_uuid(self, *, bot_uuid, port, path):
        raise AssertionError("ws-info must not be resolved in unit fakes")


class _Passport:
    """PassportPlugin stand-in: answers ``query_auth_status`` from a
    per-bot scripted application status (default PENDING), in the same
    answer shape (``{"status": ...}``) the creation job consumes; a
    missing entry means Passport has no answer yet, and ``raises`` fakes
    an outage the same fail-soft rule the job's own poll follows."""

    def __init__(self, per_bot=None, *, raises=False, answer=None):
        self.per_bot = dict(per_bot or {})
        self.raises = raises
        self.answer = answer

    def query_auth_status(self, *, bot_id, owner_workno):
        if self.raises:
            raise RuntimeError("passport outage")
        if self.answer is None and bot_id not in self.per_bot:
            return None
        if self.answer is not None:
            return self.answer
        return {"status": self.per_bot[bot_id]}


def _job(status, last_error=None, payload=None):
    job = types.SimpleNamespace(status=status, last_error=last_error)
    if payload is not None:
        job.payload = payload
    return job


def _payload(*, submitted_at=None, window_seconds=_WINDOW_SECONDS,
             iframe_url="https://consent.example/iframe",
             redirect_url="https://consent.example/redirect"):
    return {
        "iframe_url": iframe_url,
        "redirect_url": redirect_url,
        "submitted_at": submitted_at
        or (datetime.now() - timedelta(seconds=30)).isoformat(),
        "window_seconds": window_seconds,
    }


def _mk(*, seam=None, repo=None, apply_=None, passport=None):
    return AvernetBotPlatform(
        manifest_seam=seam or _Seam(),
        bot_service=_BotSvc(),
        passport_plugin=passport or _Passport(per_bot={"b1": "PENDING"}),
        skill_set_factory=types.SimpleNamespace(),
        bot_repo=repo or _Repo(),
        apply_service=apply_ or _Apply(),
        space_context=_Space(),
        baas_service=_Baas(),
        max_platform_workers=1,
    )


def _authorization(status):
    """The descriptor a run-surface consumer would read off the status
    result; ``None`` for a plain state with no gate attached."""
    return getattr(status, "authorization", None)


def _expected_deadline(payload) -> str:
    return (
        datetime.fromisoformat(payload["submitted_at"])
        + timedelta(seconds=payload["window_seconds"])
    ).isoformat()


# ── pending inside the window ────────────────────────────────────────────


class TestAwaitingAuthorization:

    def test_pending_in_window_is_awaiting_with_the_descriptor(self):
        for live in (TaskStatus.PENDING, TaskStatus.RUNNING):
            payload = _payload()
            seam = _Seam()
            seam.jobs["b1"] = _job(live, payload=payload)
            status = _mk(seam=seam).bot_status("b1", owner_id="owner-1")
            assert status == BotState.AWAITING_AUTHORIZATION.value, live
            authorization = _authorization(status)
            assert authorization is not None, live
            assert authorization.state == BotState.AWAITING_AUTHORIZATION.value
            assert authorization.iframe_url == payload["iframe_url"]
            assert authorization.redirect_url == payload["redirect_url"]
            assert authorization.deadline == _expected_deadline(payload)

    def test_urls_and_deadline_are_served_stably_from_the_job_payload(self):
        # The authorization handles ride the durable payload; repeated
        # status reads serve the same URLs and the same frozen deadline,
        # never a second passport application's.
        payload = _payload()
        seam = _Seam()
        seam.jobs["b1"] = _job(TaskStatus.RUNNING, payload=payload)
        platform = _mk(seam=seam)
        served = [
            _authorization(platform.bot_status("b1", owner_id="owner-1"))
            for _ in range(3)
        ]
        assert served[0] == served[1] == served[2]
        for authorization in served:
            assert authorization.iframe_url == payload["iframe_url"]
            assert authorization.redirect_url == payload["redirect_url"]
            assert authorization.deadline == _expected_deadline(payload)

    def test_pending_past_the_window_waits_for_the_jobs_own_answer(self):
        # PENDING past the deadline is not awaiting: the job retires the
        # creation within one poll delay and the terminal branch serves
        # AUTHORIZATION_EXPIRED from the job row — the pre-change
        # in-progress answer stands until then, and the gate never
        # front-runs the job's own discard.
        payload = _payload(
            submitted_at=(
                datetime.now() - timedelta(seconds=_WINDOW_SECONDS + 300)
            ).isoformat(),
        )
        seam = _Seam()
        seam.jobs["b1"] = _job(TaskStatus.RUNNING, payload=payload)
        platform = _mk(seam=seam)
        status = platform.bot_status("b1", owner_id="owner-1")
        assert status == BotState.PROVISIONING.value
        assert _authorization(status) is None
        seam.jobs["b1"] = _job(TaskStatus.TIMED_OUT, payload=payload)
        assert platform.bot_status("b1", owner_id="owner-1") == (
            BotState.AUTHORIZATION_EXPIRED.value
        )

    def test_passport_without_an_answer_keeps_provisioning(self):
        # A Passport that cannot answer is a wait, never a verdict — the
        # same fail-soft rule the creation job's own status poll follows.
        payload = _payload()
        seam = _Seam()
        seam.jobs["b1"] = _job(TaskStatus.RUNNING, payload=payload)
        for passport in (_Passport(), _Passport(raises=True)):
            platform = _mk(seam=seam, passport=passport)
            status = platform.bot_status("b1", owner_id="owner-1")
            assert status == BotState.PROVISIONING.value
            assert _authorization(status) is None


# ── authorization outcomes drive lifecycle transitions ───────────────────


class TestAuthorizationOutcomes:

    def test_human_rejection_is_authorization_rejected(self):
        seam = _Seam()
        seam.jobs["b1"] = _job(
            TaskStatus.FAILED, f"authorization did not complete: REJECTED")
        status = _mk(seam=seam).bot_status("b1", owner_id="owner-1")
        assert status == BotState.AUTHORIZATION_REJECTED.value
        assert _authorization(status) is None

    def test_window_elapsed_last_error_is_authorization_expired(self):
        seam = _Seam()
        seam.jobs["b1"] = _job(TaskStatus.FAILED, AUTHORIZATION_WINDOW_ELAPSED)
        assert _mk(seam=seam).bot_status("b1", owner_id="owner-1") == (
            BotState.AUTHORIZATION_EXPIRED.value
        )

    def test_queue_timeout_is_authorization_expired(self):
        seam = _Seam()
        seam.jobs["b1"] = _job(TaskStatus.TIMED_OUT)
        assert _mk(seam=seam).bot_status("b1", owner_id="owner-1") == (
            BotState.AUTHORIZATION_EXPIRED.value
        )


# ── approved and auto-issued creations take the normal path ─────────────


class TestIssuedPath:

    def test_approval_resumes_the_normal_provisioning_path_toward_ready(self):
        payload = _payload()
        seam = _Seam()
        seam.jobs["b1"] = _job(TaskStatus.RUNNING, payload=payload)
        passport = _Passport(per_bot={"b1": "PENDING"})
        repo = _Repo()
        platform = _mk(seam=seam, repo=repo, passport=passport)

        assert platform.bot_status("b1", owner_id="owner-1") == (
            BotState.AWAITING_AUTHORIZATION.value
        )

        passport.per_bot["b1"] = "ISSUED"
        status = platform.bot_status("b1", owner_id="owner-1")
        assert status == BotState.PROVISIONING.value
        assert _authorization(status) is None

        repo.rows["b1"] = {"status": "ACTIVE"}
        seam.jobs["b1"] = _job(TaskStatus.SUCCEEDED, payload=payload)
        assert platform.bot_status("b1", owner_id="owner-1") == (
            BotState.READY.value
        )

    def test_auto_issued_creation_never_awaits_even_with_urls_in_payload(self):
        # The fast path: status already ISSUED, consent URLs present in
        # the payload — the gate keys off the application status, never
        # off the URLs' presence.
        payload = _payload()
        seam = _Seam()
        seam.jobs["b1"] = _job(TaskStatus.RUNNING, payload=payload)
        platform = _mk(
            seam=seam, passport=_Passport(per_bot={"b1": "ISSUED"}))
        status = platform.bot_status("b1", owner_id="owner-1")
        assert status == BotState.PROVISIONING.value
        assert _authorization(status) is None


# ── the gate is per-bot: a mixed run reports each bot independently ─────


class TestPerBotIndependence:

    def test_mixed_concurrent_states_report_independently(self):
        payload = _payload()
        seam = _Seam()
        seam.jobs["b-awaiting"] = _job(
            TaskStatus.RUNNING, payload=_payload(
                iframe_url="https://consent.example/awaiting-iframe",
                redirect_url="https://consent.example/awaiting-redirect",
            ))
        seam.jobs["b-issued"] = _job(TaskStatus.RUNNING, payload=payload)
        seam.jobs["b-expired"] = _job(TaskStatus.TIMED_OUT)
        passport = _Passport(per_bot={
            "b-awaiting": "PENDING",
            "b-issued": "ISSUED",
            "b-expired": "PENDING",
        })
        platform = _mk(seam=seam, passport=passport)

        for _ in range(2):
            awaiting = platform.bot_status("b-awaiting", owner_id="owner-1")
            assert awaiting == BotState.AWAITING_AUTHORIZATION.value
            assert _authorization(awaiting).iframe_url == (
                "https://consent.example/awaiting-iframe"
            )
            assert _authorization(awaiting).redirect_url == (
                "https://consent.example/awaiting-redirect"
            )
            assert _authorization(awaiting).deadline == _expected_deadline(
                seam.jobs["b-awaiting"].payload)

            issued = platform.bot_status("b-issued", owner_id="owner-1")
            assert issued == BotState.PROVISIONING.value
            assert _authorization(issued) is None

            expired = platform.bot_status("b-expired", owner_id="owner-1")
            assert expired == BotState.AUTHORIZATION_EXPIRED.value
            assert _authorization(expired) is None